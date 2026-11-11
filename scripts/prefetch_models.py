"""Download Whisper models once, at home, before the event (venue Wi-Fi may be slow).

    uv run python scripts/prefetch_models.py            # tiny.en + base.en
    uv run python scripts/prefetch_models.py small.en   # better quality, ~480 MB

Models are cached by Hugging Face in your user folder, not in the repo.
This only DOWNLOADS public model files; it never sends any of our data anywhere.
"""
import sys

from faster_whisper import WhisperModel

names = sys.argv[1:]
if not names:
    names = ["tiny.en", "base.en"]

for name in names:
    print(f"Downloading / loading {name} ...")
    WhisperModel(name, device="cpu", compute_type="int8")
    print(f"  {name} ready")
