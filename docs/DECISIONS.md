# Decisions log

Append one line per decision, newest at the bottom: `YYYY-MM-DD HH:MM | who | decision | why`

2026-11-11 23:00 | Reinis | Python 3.12 + uv, one codebase (CLI + local Flask UI) | team uses Windows/macOS/Linux; best libraries for audio/PDF/images are Python; team already knows Python
2026-11-11 23:00 | Reinis | Local web app on 127.0.0.1, not desktop GUI, not cloud | runs on all 3 OSes, jury sees a browser demo, confidential data never leaves the laptop
2026-11-11 23:00 | Reinis | faster-whisper, pinned av<16 | PyAV 16+ breaks faster-whisper 1.2 decode_audio (tested)
2026-11-11 23:00 | Reinis | pypdfium2 instead of PyMuPDF | PyMuPDF is AGPL; pypdfium2 is Apache/BSD, matters if Ericsson reuses the code
2026-11-11 23:00 | Reinis | Key frames by 1 fps sampling + dHash, not ffmpeg scene detection | slide changes score only 0.19–0.25; sampling also needs no ffmpeg install
2026-11-11 23:00 | Reinis | Thresholds: frame keep >=6, slide match <=6, visual dup <=3, revisions by text | measured on fixtures, DATA_CONTRACT 6
2026-11-11 23:00 | Reinis | Ericsson sample data never committed or uploaded | confidentiality
