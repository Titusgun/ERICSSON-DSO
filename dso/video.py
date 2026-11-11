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

Re-run safe: DELETE FROM frames / segments WHERE file_id = ? before inserting.
"""


def run(args):
    print("[video] TODO (owner A): key frames + transcript")
    return 0
