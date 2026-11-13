"""`dso video` (owner: A)

For every file with kind='video' in the DB:

1. Key frames (simple and robust for slide videos):
   - open the video with cv2.VideoCapture(path), read frames, look at 1 frame per second
     (frame_index % round(fps) == 0)
   - dhash_image() each sampled frame (convert BGR -> RGB -> PIL first)
   - keep a frame if it is the first one, or if it differs from the last KEPT frame
     (distance >= 6) AND the screen then stays still for a second (the next sample
     looks the same). Fallback: if the screen never settles, keep one every 60 s.
     See keep_decision() for why.
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

Re-run safe: DELETE FROM frames / segments WHERE file_id = ? before inserting.

Already processed videos are SKIPPED (key frames in the DB and a matching
transcript cache), so `dso all` / `dso watch` only work on new videos. Use
--force to redo them, e.g. after changing the key-frame rule.
Also sets files.duration_sec (DATA_CONTRACT v1.1).
"""
import json
import shutil
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

# If the screen keeps changing and never settles (camera on a person, a video
# inside the shared screen), still keep one frame every this many seconds, so
# `dso link` and the export always have a picture of what was on screen.
MAX_GAP_SEC = 60

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


def clear_old_frames(conn, file_id):
    """Removes the frame ROWS of an earlier run of this video (not committed yet).

    segments.frame_id points into frames, and SQLite checks that link
    (PRAGMA foreign_keys = ON), so the segments are unlinked first. `dso link`
    sets them again for the new frames.
    The old WebP files are only replaced after the new rows are committed
    (swap_frame_folders), so a run that is stopped halfway never leaves rows
    that point at deleted pictures.
    """
    conn.execute("UPDATE segments SET frame_id = NULL WHERE file_id = ?", (file_id,))
    conn.execute("DELETE FROM frames WHERE file_id = ?", (file_id,))


# Written next to the key frames: which part of the video they cover. A long
# video whose transcript is complete must not count as "done" when its key
# frames were only taken for the first --max-seconds (dso cleanup reads it too).
FRAMES_INFO = "frames_info.json"


def frames_cover(out_dir, sha256, wanted_max_seconds):
    """Do the key frames in out/frames/<sha12>/ cover the requested part?
    Frames from before this file existed were always taken for the whole
    video, so a missing file counts as "whole video"."""
    path = out_dir / "frames" / sha12(sha256) / FRAMES_INFO
    try:
        with open(path, encoding="utf-8") as f:
            done_max_seconds = json.load(f).get("max_seconds")
    except (OSError, ValueError):
        return True
    return covers(done_max_seconds, wanted_max_seconds)


def swap_frame_folders(new_dir, frames_dir):
    """Replaces out/frames/<sha12>/ with the freshly written <sha12>.new/.
    Only our own output in out/frames/ is touched, never user files."""
    if frames_dir.is_dir():
        shutil.rmtree(frames_dir)
    new_dir.rename(frames_dir)


def keep_decision(prev, current, last_kept):
    """Decides, at every 1-per-second sample, whether to keep a key frame.

    prev, current and last_kept are dicts with "t_sec" and "hash" (prev is the
    sample one second before current; last_kept is the last frame we saved).
    Returns "prev", "current" or None (= keep nothing now).

    Why not simply "keep every sample that differs from the last kept frame"?
    On the real Teams recording that kept 840 frames in 2.5 h, mostly 1-2 s
    apart: transitions, scrolling, Teams switching between the shared screen
    and the speaker, a moving camera. Each of those passing moments became a
    key frame. A slide is different: it STAYS on screen. So:

    1. "prev": the screen changed at prev (differs from the last kept frame)
       AND one second later it still looks the same (current is close to
       prev) -> the screen settled on something new: keep prev. We keep prev,
       not current, so the frame gets the time the change happened.
    2. "current": fallback when the screen never settles. If it differs from
       the last kept frame and that frame is MAX_GAP_SEC or more old, keep the
       current sample anyway.

    We do NOT skip a frame because it looks like an older kept frame
    (A -> B -> A). Going back to an earlier slide must create a frame, or
    `dso link` would point the transcript at slide B while A is on screen.
    """
    if prev is not None:
        changed = hamming(prev["hash"], last_kept["hash"]) >= KEEP_MIN_DIST
        settled = hamming(current["hash"], prev["hash"]) < KEEP_MIN_DIST
        if changed and settled:
            return "prev"
    changed_now = hamming(current["hash"], last_kept["hash"]) >= KEEP_MIN_DIST
    waited_long = current["t_sec"] - last_kept["t_sec"] >= MAX_GAP_SEC
    if changed_now and waited_long:
        return "current"
    return None


def save_frame(conn, file_row, write_dir, short_id, number, sample):
    """Writes one key frame as WebP into `write_dir` and inserts its row into
    `frames` with its final path frames/<sha12>/fNNNN.webp."""
    file_name = f"f{number:04d}.webp"         # f0001.webp, f0002.webp, ...
    sample["image"].save(write_dir / file_name, "WEBP", quality=WEBP_QUALITY)
    image_bytes = (write_dir / file_name).stat().st_size
    # Paths in the DB are relative to --out and use "/" (DATA_CONTRACT 3).
    image_path = f"frames/{short_id}/{file_name}"
    conn.execute(
        """INSERT INTO frames (file_id, t_sec, image_path, image_bytes, dhash)
           VALUES (?, ?, ?, ?, ?)""",
        (file_row["id"], round(sample["t_sec"], 3), image_path, image_bytes, sample["hash"]))


def extract_key_frames(conn, file_row, out_dir, max_seconds):
    """Step 1 for one video: sample 1 frame per second, keep the ones where the
    screen changed and then stayed still (see keep_decision()).

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
    if duration > 0:
        # The whole video's length, also with --max-seconds (DATA_CONTRACT v1.1).
        conn.execute("UPDATE files SET duration_sec = ? WHERE id = ?", (round(duration, 2), file_row["id"]))

    clear_old_frames(conn, file_row["id"])
    # Write the new pictures next to the old ones; swap after the commit.
    new_dir = out_dir / "frames" / (short_id + ".new")
    if new_dir.is_dir():
        shutil.rmtree(new_dir)                  # left over from a stopped run
    new_dir.mkdir(parents=True)

    kept = 0
    prev = None           # the sample one second ago: {"t_sec", "hash", "image"}
    last_kept = None      # the last frame we saved:   {"t_sec", "hash", "image"}
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
        current = {"t_sec": t_sec, "hash": dhash_image(image), "image": image}

        if last_kept is None:
            to_keep = current                       # always keep the very first frame
        else:
            decision = keep_decision(prev, current, last_kept)
            if decision == "prev":
                to_keep = prev
            elif decision == "current":
                to_keep = current
            else:
                to_keep = None
        if to_keep is not None:
            kept = kept + 1
            save_frame(conn, file_row, new_dir, short_id, kept, to_keep)
            last_kept = to_keep
        prev = current

        if t_sec >= next_progress:
            print(f"[video]   {format_time(t_sec)} / {format_time(duration)}, {kept} key frames")
            next_progress = next_progress + PROGRESS_EVERY_SEC
        frame_index = frame_index + 1

    cap.release()
    with open(new_dir / FRAMES_INFO, "w", encoding="utf-8") as f:
        json.dump({"max_seconds": max_seconds}, f)
    # Save the new rows first, then replace the pictures: if the run stops
    # before this point, the old rows and the old pictures are both still there.
    conn.commit()
    swap_frame_folders(new_dir, frames_dir)
    return kept


# ---------- Step 2: transcript ----------

def covers(cached_max_seconds, wanted_max_seconds):
    """Does a transcript made with --max-seconds `cached` contain everything a
    run with --max-seconds `wanted` needs? None means the whole video.
    A full transcript covers every limit; a 2-minute one never covers a full run.
    """
    if cached_max_seconds is None:
        return True
    if wanted_max_seconds is None:
        return False
    return cached_max_seconds >= wanted_max_seconds


def load_cached_transcript(json_path, model_name, max_seconds):
    """Returns the cached transcript dict if it fits this run, else None.

    "Fits" = made with the same model, and it covers the requested part (see
    covers()). A transcript of the first 5 minutes must not be reused for a full
    run, a tiny.en transcript not for small.en - but a FULL transcript is fine
    for a `--max-seconds 120` run (so a demo with --max-seconds never replaces
    finished full results).
    """
    if not json_path.is_file():
        return None
    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None                       # unreadable / half-written file: redo it
    if data.get("model") != model_name or not covers(data.get("max_seconds"), max_seconds):
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


def transcript_progress_step(audio_sec):
    """Seconds of audio between progress lines: every 10 % of the audio, but at
    least every 30 s and at most every 5 minutes. A 2-minute demo video shows
    progress live; a 3-hour recording does not flood the terminal."""
    return max(30.0, min(float(PROGRESS_EVERY_SEC), audio_sec / 10))


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
    step = transcript_progress_step(audio_sec)
    next_progress = step
    # `segments` is a generator: the actual transcription happens while we loop.
    for s in segments:
        text = s.text.strip()
        if text != "":
            result_segments.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": text})
        if s.end >= next_progress:
            done = min(100, round(100 * s.end / audio_sec)) if audio_sec > 0 else 100
            print(f"[video]   transcribed {format_time(s.end)} / {format_time(audio_sec)} ({done} %), "
                  f"{len(result_segments)} segments")
            next_progress = next_progress + step
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


def already_processed(conn, file_row, out_dir, model_name, max_seconds):
    """True if this video already has key frames in the DB that cover the
    requested part, and a transcript cache (same model) that covers it too.
    Then there is nothing new to do: `dso all` and `dso watch` skip it instead
    of spending minutes on it."""
    frame_count = conn.execute(
        "SELECT COUNT(*) FROM frames WHERE file_id = ?", (file_row["id"],)).fetchone()[0]
    if frame_count == 0 or not frames_cover(out_dir, file_row["sha256"], max_seconds):
        return False
    json_path = out_dir / "transcripts" / f"{sha12(file_row['sha256'])}.json"
    return load_cached_transcript(json_path, model_name, max_seconds) is not None


def run(args):
    conn = connect(args.db)
    out_dir = Path(args.out)
    # getattr(..., None): works before and after dso/cli.py gets --max-seconds (#17).
    max_seconds = getattr(args, "max_seconds", None)
    force = getattr(args, "force", False)      # --force: redo already processed videos

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

        if not force and already_processed(conn, file_row, out_dir, args.model, max_seconds):
            print(f"[video] {file_row['name']}: already processed - skipped (--force to redo)")
            continue

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
