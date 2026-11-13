"""Download the models once, at home, before the event (venue Wi-Fi may be slow).

    uv run python scripts/prefetch_models.py            # Whisper "small" + the speaker models
    uv run python scripts/prefetch_models.py base.en    # other Whisper models, by name

Whisper models are cached by Hugging Face in your user folder; the speaker models
(sherpa-onnx, ~34 MB) in ~/.cache/dso-models. Nothing goes into the repo.
This only DOWNLOADS public model files; it never sends any of our data anywhere.
"""
import sys

from faster_whisper import WhisperModel

from dso import speakers

names = sys.argv[1:]
if not names:
    names = ["small"]                  # the default model of `dso video` (multilingual)

for name in names:
    print(f"Downloading / loading Whisper {name} ...")
    WhisperModel(name, device="cpu", compute_type="int8")
    print(f"  {name} ready")

print("Downloading the speaker models (sherpa-onnx) ...")
speakers.ensure_models()
print(f"  ready in {speakers.MODEL_DIR}")
