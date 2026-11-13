"""`dso video` (owner: A)

For every file with kind='video' in the DB:

1. Key frames (simple and robust for slide videos):
   - open the video with cv2.VideoCapture(path), read frames, look at 1 frame per second
     (frame_index % round(fps) == 0)
   - dhash_image() each sampled frame (convert BGR -> RGB -> PIL first)
   - keep a frame if it is the first one, or its distance to the last KEPT frame is >= 6
   - save kept frames as WebP: out/frames/<sha12>/f0001.webp (Pillow: img.save(p, "WEBP", quality=60))
   - insert into `frames` (t_sec, image_path, image_bytes, dhash)
   Fixture check: tests/fixtures/video/talk.mp4 -> 3 frames at t = 0, 8, 16.
   (Why not ffmpeg scene detection: slide changes score only 0.19-0.25, see DATA_CONTRACT 6.)

2. Transcript with faster-whisper (no ffmpeg needed - it decodes audio from the mp4 itself):
       from faster_whisper import WhisperModel
       model = WhisperModel(args.model, device="cpu", compute_type="int8")
       segments, info = model.transcribe(path, vad_filter=True)
       for s in segments: s.start, s.end, s.text
   Insert every segment into `segments` with speaker "Speaker ?".
   Also write the raw segments to out/transcripts/<sha12>.json.
   The first run downloads the model from Hugging Face (tiny.en ~75 MB, base.en ~145 MB,
   small.en ~480 MB) - do this at home before the event.
   The fixture audio is only a tone (no speech), so 0 segments there is expected.

   Transcribing hours of audio on a CPU takes a long time, so the result is CACHED:
   if out/transcripts/<sha12>.json exists and was made with the same model and the
   same --max-seconds, it is loaded instead of transcribing again. Delete that
   file to force a new transcription.

--max-seconds N (issue #17): only the first N seconds of each video are processed.
   dso/cli.py (owner D) still has to add the option; until then args has no
   max_seconds attribute and the whole video is processed.

Re-run safe: DELETE FROM frames / segments WHERE file_id = ? before inserting.
"""
import json
from pathlib import Path

import cv2
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio
from PIL import Image

from dso.common.db import connect
from dso.common.hashing import dhash_image, hamming, sha12

# DATA_CONTRACT 6: keep a sampled frame if its dHash differs from the last KEPT
# frame in at least this many bits (out of 64). Same slide = 0-4, new slide = 8+.
KEEP_MIN_DIST = 6

# DATA_CONTRACT 3: key frames are WebP with quality 60.
WEBP_QUALITY = 60

# Print a progress line every N seconds of video (= every 5 minutes).
PROGRESS_EVERY_SEC = 300

# Whisper works on 16 kHz mono audio: 16000 numbers per second of sound.
SAMPLE_RATE = 16000


def format_time(seconds):
    """96.0 -> "01:36", 3725.0 -> "1:02:05"."""
    total = int(seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def clear_old_frames(conn, file_id, frames_dir):
    """Removes the frames of an earlier run of this video (rows + WebP files).

    segments.frame_id points into frames, and SQLite checks that link
    (PRAGMA foreign_keys = ON), so the segments are unlinked first. `dso link`
    sets them again for the new frames.
    The WebP files are our own output in out/frames/, never user files.
    """
    conn.execute("UPDATE segments SET frame_id = NULL WHERE file_id = ?", (file_id,))
    conn.execute("DELETE FROM frames WHERE file_id = ?", (file_id,))
    if frames_dir.is_dir():
        for old_file in frames_dir.glob("f*.webp"):
            old_file.unlink()


def extract_key_frames(conn, file_row, out_dir, max_seconds):
    """Step 1 for one video: sample 1 frame per second, keep the ones that changed.

    Returns the number of kept frames, or None if the video could not be opened.
    """
    video_path = Path(file_row["path"])
    short_id = sha12(file_row["sha256"])
    frames_dir = out_dir / "frames" / short_id

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"[video]   cannot open {video_path.as_posix()} - skipped")
        return None

    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    if fps <= 0:
        fps = 25.0     # some files do not report fps; 25 is the most common value
    # Every `step`-th frame is one second apart (30 fps -> every 30th frame).
    step = max(1, round(fps))
    duration = frame_count / fps if frame_count > 0 else 0.0
    print(f"[video]   {format_time(duration)} at {fps:.1f} fps -> 1 sample per second")

    clear_old_frames(conn, file_row["id"], frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)

    kept = 0
    last_kept_hash = None
    frame_index = 0
    next_progress = PROGRESS_EVERY_SEC
    while True:
        # grab() moves to the next frame without converting it to a picture,
        # which is cheaper. We only retrieve() the 1-per-second samples.
        if not cap.grab():
            break                                   # end of the video
        if frame_index % step != 0:
            frame_index = frame_index + 1
            continue

        t_sec = frame_index / fps
        if max_seconds is not None and t_sec > max_seconds:
            break                                   # --max-seconds reached

        ok, bgr = cap.retrieve()
        if not ok:
            break
        # OpenCV gives colours as Blue-Green-Red; Pillow expects Red-Green-Blue.
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        frame_hash = dhash_image(image)

        # Keep the first frame, and every frame that looks different enough
        # from the last frame we kept (= a new slide or a new scene).
        if last_kept_hash is None or hamming(frame_hash, last_kept_hash) >= KEEP_MIN_DIST:
            kept = kept + 1
            file_name = f"f{kept:04d}.webp"         # f0001.webp, f0002.webp, ...
            image.save(frames_dir / file_name, "WEBP", quality=WEBP_QUALITY)
            image_bytes = (frames_dir / file_name).stat().st_size
            # Paths in the DB are relative to --out and use "/" (DATA_CONTRACT 3).
            image_path = f"frames/{short_id}/{file_name}"
            conn.execute(
                """INSERT INTO frames (file_id, t_sec, image_path, image_bytes, dhash)
                   VALUES (?, ?, ?, ?, ?)""",
                (file_row["id"], round(t_sec, 3), image_path, image_bytes, frame_hash))
            last_kept_hash = frame_hash

        if t_sec >= next_progress:
            print(f"[video]   {format_time(t_sec)} / {format_time(duration)}, {kept} key frames")
            next_progress = next_progress + PROGRESS_EVERY_SEC
        frame_index = frame_index + 1

    cap.release()
    return kept


# ---------- Step 2: transcript ----------

def load_cached_transcript(json_path, model_name, max_seconds):
    """Returns the cached transcript dict if it fits this run, else None.

    "Fits" = made with the same model and the same --max-seconds. A transcript
    of the first 5 minutes must not be reused for a full run, and a tiny.en
    transcript must not be reused when you ask for small.en.
    """
    if not json_path.is_file():
        return None
    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None                       # unreadable / half-written file: redo it
    if data.get("model") != model_name or data.get("max_seconds") != max_seconds:
        return None
    return data


def is_multilingual(model_name):
    """True for models that know many languages ("small", "base"), False for
    English-only models ("base.en", "small.en")."""
    return not model_name.endswith(".en")


def whisper_options(model_name):
    """Options for model.transcribe().

    vad_filter=True: "voice activity detection" skips silence, which is faster
    and stops Whisper from inventing text during long pauses.

    multilingual=True: detect the language again for every 30-second window,
    instead of once at the start. Our recordings are English, but people speak
    Swedish during breaks (decision 2026-11-13 in DECISIONS.md); without this the
    Swedish parts would be forced into English nonsense. English-only (.en)
    models cannot do this, so the option is only set for multilingual models.
    """
    options = {"vad_filter": True}
    if is_multilingual(model_name):
        options["multilingual"] = True
    return options


def run_whisper(model, model_name, video_path, max_seconds):
    """Transcribes one video. Returns the transcript dict that is cached as JSON.

    faster-whisper decodes the audio track from the mp4 itself (no ffmpeg).
    We decode it first so --max-seconds can cut it: the audio is a list of
    numbers, 16000 per second, so the first N seconds = the first N * 16000.
    """
    audio = decode_audio(str(video_path), sampling_rate=SAMPLE_RATE)
    if max_seconds is not None:
        audio = audio[:int(max_seconds * SAMPLE_RATE)]
    audio_sec = len(audio) / SAMPLE_RATE
    print(f"[video]   transcribing {format_time(audio_sec)} of audio (this is the slow part)")

    segments, info = model.transcribe(audio, **whisper_options(model_name))
    # With a multilingual model this is the language of the START of the video;
    # later windows (e.g. Swedish breaks) are detected separately.
    print(f"[video]   language at the start: {info.language} "
          f"(probability {info.language_probability:.2f})")
    if not is_multilingual(model_name):
        print(f"[video]   note: {model_name} is English-only; other languages become nonsense")

    result_segments = []
    next_progress = PROGRESS_EVERY_SEC
    # `segments` is a generator: the actual transcription happens while we loop.
    for s in segments:
        text = s.text.strip()
        if text != "":
            result_segments.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": text})
        if s.end >= next_progress:
            print(f"[video]   {format_time(s.end)} / {format_time(audio_sec)}, "
                  f"{len(result_segments)} segments")
            next_progress = next_progress + PROGRESS_EVERY_SEC
    return {
        "model": None,                    # filled in by the caller
        "max_seconds": max_seconds,
        "language": info.language,
        "language_probability": round(info.language_probability, 3),
        "audio_sec": round(audio_sec, 2),
        "segments": result_segments,
    }


def transcribe(conn, file_row, out_dir, model_name, max_seconds, model_holder):
    """Step 2 for one video: fills `segments` and out/transcripts/<sha12>.json.

    model_holder is a dict {"model": None}. The Whisper model is loaded on first
    use only (it takes a few seconds), and never if every transcript is cached.
    Returns the number of segments, or None if the video has no readable audio.
    """
    short_id = sha12(file_row["sha256"])
    json_path = out_dir / "transcripts" / f"{short_id}.json"

    data = load_cached_transcript(json_path, model_name, max_seconds)
    if data is not None:
        print(f"[video]   transcript: reusing {json_path.as_posix()} (delete it to transcribe again)")
    else:
        if model_holder["model"] is None:
            print(f"[video]   loading Whisper model {model_name} ...")
            model_holder["model"] = WhisperModel(model_name, device="cpu", compute_type="int8")
        try:
            data = run_whisper(model_holder["model"], model_name, Path(file_row["path"]), max_seconds)
        except Exception as error:
            # e.g. a video without an audio track. Skip it, do not stop the whole run.
            print(f"[video]   transcript skipped, cannot read audio: {error}")
            return None
        data["model"] = model_name
        json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # Replace the old rows of this video (re-run safe). Their frame links are
    # set again by `dso link`.
    conn.execute("DELETE FROM segments WHERE file_id = ?", (file_row["id"],))
    for s in data["segments"]:
        conn.execute(
            """INSERT INTO segments (file_id, start_sec, end_sec, speaker, text)
               VALUES (?, ?, ?, 'Speaker ?', ?)""",
            (file_row["id"], s["start"], s["end"], s["text"]))
    return len(data["segments"])


def run(args):
    conn = connect(args.db)
    out_dir = Path(args.out)
    # getattr(..., None): works before and after dso/cli.py gets --max-seconds (#17).
    max_seconds = getattr(args, "max_seconds", None)

    videos = conn.execute(
        "SELECT id, path, name, sha256 FROM files WHERE kind = 'video' ORDER BY id").fetchall()
    print(f"[video] {len(videos)} videos")
    if max_seconds is not None:
        print(f"[video] only the first {max_seconds} seconds of each video (--max-seconds)")

    model_holder = {"model": None}      # Whisper model, loaded on first use
    done_hashes = set()     # exact duplicate videos are processed only once
    for file_row in videos:
        if file_row["sha256"] in done_hashes:
            print(f"[video] {file_row['name']}: exact duplicate, already processed - skipped")
            continue
        done_hashes.add(file_row["sha256"])

        print(f"[video] {file_row['name']}")
        kept = extract_key_frames(conn, file_row, out_dir, max_seconds)
        # Commit after every step, so a crash in video 3 keeps videos 1 and 2,
        # and a crash in the transcript keeps the key frames.
        conn.commit()
        if kept is None:
            continue                        # could not open the video at all
        print(f"[video]   {kept} key frames -> out/frames/{sha12(file_row['sha256'])}/")

        count = transcribe(conn, file_row, out_dir, args.model, max_seconds, model_holder)
        conn.commit()
        if count is not None:
            print(f"[video]   {count} transcript segments")

    conn.close()
    return 0
