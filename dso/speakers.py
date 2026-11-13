"""`dso speakers` (owner: A) - who spoke when ("speaker diarization").

The brief: "In a recording, capture the text ... with each speaker identified."
Whisper gives WHAT was said; this step adds WHO said it. It does not know names,
only voices: every segment gets "Speaker 1", "Speaker 2", ... (numbered in the
order they first speak). segments.speaker already exists in the contract
("Speaker ?" = unknown), so export, search and the UI show it automatically.

How (sherpa-onnx, runs locally on the CPU, nothing is sent anywhere):
  1. the audio is decoded the same way as for the transcript (16 kHz mono)
  2. a pyannote segmentation model finds where speech is and where the speaker
     changes; a WeSpeaker model turns each piece into a "voice fingerprint";
     pieces with similar fingerprints are grouped (clustered) into speakers
  3. each transcript segment gets the speaker who talks the longest during it

Models: two public files (~34 MB), downloaded once into ~/.cache/dso-models
(`uv run python scripts/prefetch_models.py` also gets them).
Cache: out/speakers/<sha12>.json, so a 2.5 h meeting is analysed only once. It
is redone when the transcript covers a different part of the video, or the
models / the CLUSTER_THRESHOLD change.

Runs after `video` (it needs the transcript). Safe to run twice: it overwrites
segments.speaker. If the models cannot be loaded (e.g. offline on the first
run) it prints a warning and leaves "Speaker ?" - the rest of the pipeline goes on.
"""
import json
import os
import tarfile
import urllib.request
from pathlib import Path

from faster_whisper.audio import decode_audio

from dso.common.db import connect
from dso.common.hashing import sha12

MODEL_DIR = Path.home() / ".cache" / "dso-models"
RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
SEGMENTATION_URL = f"{RELEASES}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
SEGMENTATION_MODEL = MODEL_DIR / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
EMBEDDING_URL = f"{RELEASES}/speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx"
EMBEDDING_MODEL = MODEL_DIR / "wespeaker_en_voxceleb_resnet34_LM.onnx"
MODEL_ID = "pyannote-segmentation-3.0 + wespeaker-en-voxceleb-resnet34-LM"

# How different two voices must be to count as two speakers (cosine distance,
# sherpa-onnx's FastClustering). Lower = more speakers. Measured 14 Nov on 12 min
# of the real Teams meeting (a song, a Q&A with several people, a presenter
# taking questions - about 4-6 real voices): 0.5 -> 6 voices, 0.65 -> 3,
# 0.8 -> 2 (Q&A people merged), 0.95 -> 1. 0.6: a bit more merging of one
# person over 2.5 h than 0.5, without lumping the Q&A speakers together.
CLUSTER_THRESHOLD = 0.6

# A "speaker" heard for less than this in a whole video is noise (a cough, a
# music sting, one line of a film clip): its lines go to the speaker who overlaps
# them, or "Speaker ?". Real Teams meeting with 5 s: 41 speakers, 17 of them with
# under 30 s in total (2.5 h) - too many to read. With 30 s those disappear.
MIN_SPEAKER_SEC = 30.0

SAMPLE_RATE = 16000
UNKNOWN = "Speaker ?"


# ---------- models ----------

def download(url, target):
    """Downloads one public model file (to a .part file first, so a broken
    download is never mistaken for a model)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    print(f"[speakers] downloading {url.rsplit('/', 1)[-1]} (once) ...")
    urllib.request.urlretrieve(url, part)
    part.replace(target)


def ensure_models():
    """Makes sure both model files are in MODEL_DIR."""
    if not SEGMENTATION_MODEL.is_file():
        archive = MODEL_DIR / "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
        download(SEGMENTATION_URL, archive)
        with tarfile.open(archive, "r:bz2") as tar:
            tar.extractall(MODEL_DIR, filter="data")    # "data": no files outside MODEL_DIR
        archive.unlink()
    if not EMBEDDING_MODEL.is_file():
        download(EMBEDDING_URL, EMBEDDING_MODEL)


def build_diarizer():
    """The sherpa-onnx speaker diarization, using all CPU cores."""
    import sherpa_onnx                       # imported here: only needed for this step
    threads = os.cpu_count() or 1
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(SEGMENTATION_MODEL)),
            num_threads=threads),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(EMBEDDING_MODEL), num_threads=threads),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=CLUSTER_THRESHOLD),
        min_duration_on=0.3, min_duration_off=0.5)
    if not config.validate():
        raise RuntimeError("sherpa-onnx speaker diarization config is not valid (model files?)")
    return sherpa_onnx.OfflineSpeakerDiarization(config)


# ---------- who spoke when ----------

def drop_short_speakers(turns):
    """Removes the turns of speakers heard for less than MIN_SPEAKER_SEC in total."""
    total = {}
    for turn in turns:
        total[turn["speaker"]] = total.get(turn["speaker"], 0.0) + turn["end"] - turn["start"]
    kept = []
    for turn in turns:
        if total[turn["speaker"]] >= MIN_SPEAKER_SEC:
            kept.append(turn)
    return kept


def renumber(turns):
    """sherpa-onnx numbers speakers 0, 1, 2 in no particular order. Renumber them
    1, 2, 3 in the order they first speak, so "Speaker 1" opens the recording.
    turns: list of {"start", "end", "speaker"} sorted by start."""
    new_numbers = {}
    result = []
    for turn in turns:
        if turn["speaker"] not in new_numbers:
            new_numbers[turn["speaker"]] = len(new_numbers) + 1
        result.append({"start": turn["start"], "end": turn["end"], "speaker": new_numbers[turn["speaker"]]})
    return result


def speaker_for(start, end, turns):
    """The speaker who talks the longest between start and end, as "Speaker N",
    or UNKNOWN if nobody does (e.g. music)."""
    talked = {}
    for turn in turns:
        overlap = min(end, turn["end"]) - max(start, turn["start"])
        if overlap > 0:
            talked[turn["speaker"]] = talked.get(turn["speaker"], 0.0) + overlap
    if len(talked) == 0:
        return UNKNOWN
    best = None
    for speaker, seconds in talked.items():
        if best is None or seconds > talked[best]:
            best = speaker
    return f"Speaker {best}"


def diarize(diarizer, audio):
    """Runs the diarization with progress every 10 %. Returns the raw turns
    (sherpa-onnx's own speaker numbers), sorted by start. They are cached as
    they are; drop_short_speakers() and renumber() are applied when labelling,
    so changing those rules does not need the slow analysis again."""
    progress = {"next": 10}

    def on_progress(done, total):
        percent = 100 * done // max(total, 1)
        if percent >= progress["next"]:
            print(f"[speakers]   {percent} % of the audio analysed")
            progress["next"] = percent - percent % 10 + 10
        return 0                                 # 0 = continue

    result = diarizer.process(audio, callback=on_progress).sort_by_start_time()
    turns = []
    for piece in result:
        turns.append({"start": round(piece.start, 2), "end": round(piece.end, 2), "speaker": piece.speaker})
    return turns


# ---------- cache + one video ----------

def transcript_max_seconds(out_dir, sha256):
    """--max-seconds of the transcript cache, so speakers cover the same part.
    Returns (found, max_seconds)."""
    path = out_dir / "transcripts" / f"{sha12(sha256)}.json"
    try:
        with open(path, encoding="utf-8") as f:
            return True, json.load(f).get("max_seconds")
    except (OSError, ValueError):
        return False, None


def load_cache(path, max_seconds):
    """The cached turns if they were made with the same models, threshold and
    for the same part of the video; else None."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    same = (data.get("model") == MODEL_ID and data.get("threshold") == CLUSTER_THRESHOLD
            and data.get("max_seconds") == max_seconds)
    if same:
        return data["turns"]
    return None


def turns_for_video(file_row, out_dir, max_seconds, holder):
    """Speaker turns of one video: from the cache, or computed and cached.
    holder = {"diarizer": None}: the models are loaded on first use only."""
    path = out_dir / "speakers" / f"{sha12(file_row['sha256'])}.json"
    turns = load_cache(path, max_seconds)
    if turns is not None:
        print(f"[speakers]   reusing {path.as_posix()} (delete it to analyse again)")
        return turns
    if holder["diarizer"] is None:
        ensure_models()
        holder["diarizer"] = build_diarizer()
    audio = decode_audio(str(file_row["path"]), sampling_rate=SAMPLE_RATE)
    if max_seconds is not None:
        audio = audio[:int(max_seconds * SAMPLE_RATE)]
    print(f"[speakers]   analysing {len(audio) / SAMPLE_RATE / 60:.1f} min of audio ...")
    turns = diarize(holder["diarizer"], audio)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"model": MODEL_ID, "threshold": CLUSTER_THRESHOLD, "max_seconds": max_seconds,
                   "turns": turns}, f, indent=1)
    return turns


def label_segments(conn, file_id, raw_turns):
    """Writes "Speaker N" into every segment of this video (short "speakers"
    dropped, numbered by first appearance). Returns
    (number of speakers, labelled segments, all segments)."""
    turns = renumber(drop_short_speakers(raw_turns))
    rows = conn.execute("SELECT id, start_sec, end_sec FROM segments WHERE file_id = ?", (file_id,)).fetchall()
    labelled = 0
    speakers = set()
    for row in rows:
        label = speaker_for(row["start_sec"], row["end_sec"], turns)
        conn.execute("UPDATE segments SET speaker = ? WHERE id = ?", (label, row["id"]))
        if label != UNKNOWN:
            labelled += 1
            speakers.add(label)
    return len(speakers), labelled, len(rows)


def run(args):
    conn = connect(args.db)
    out_dir = Path(args.out)
    videos = conn.execute(
        """SELECT f.id, f.name, f.path, f.sha256 FROM files f
           WHERE f.kind = 'video' AND EXISTS (SELECT 1 FROM segments s WHERE s.file_id = f.id)
           ORDER BY f.id""").fetchall()
    print(f"[speakers] {len(videos)} videos with a transcript")
    holder = {"diarizer": None}
    for file_row in videos:
        found, max_seconds = transcript_max_seconds(out_dir, file_row["sha256"])
        if not found:
            continue
        print(f"[speakers] {file_row['name']}")
        try:
            turns = turns_for_video(file_row, out_dir, max_seconds, holder)
        except Exception as error:
            # e.g. offline on the first run (models not downloaded) or no audio.
            print(f"[speakers]   skipped ({error}); segments stay '{UNKNOWN}'")
            continue
        speakers, labelled, total = label_segments(conn, file_row["id"], turns)
        conn.commit()
        print(f"[speakers]   {speakers} speakers, {labelled}/{total} segments labelled")
    conn.close()
    return 0
