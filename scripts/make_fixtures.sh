#!/usr/bin/env bash
# Creates small SYNTHETIC test data in tests/fixtures/ (safe to commit and share):
#   docs/deck_a.pdf        3 slides
#   docs/deck_a (1).pdf    exact duplicate of deck_a with a download suffix
#   docs/deck_b.pdf        3 slides, 2 of them identical to deck_a (shared slides)
#   docs/deck_a_rev2.pdf   revision of deck_a: slide 2 slightly changed
#   video/talk.mp4         ~24 s video showing deck_a slides, with a tone as audio
#   offboarding/jane/...   a departing employee's folder: one duplicate + one unique file
# Needs: libreoffice (soffice), poppler-utils (pdftoppm, pdfunite), ffmpeg. Only needed to REGENERATE fixtures; they are committed.
set -euo pipefail
cd "$(dirname "$0")/.."

OUT=tests/fixtures
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$OUT/docs" "$OUT/video" "$OUT/offboarding/jane"

# Each slide is its own small 16:9 HTML page -> PDF; pdfunite joins slides into a deck.
# Identical slides therefore render to identical pages in every deck.
slide() { # $1 = short id, $2 = colour, $3 = title, $4 = body ; prints the path of the one-page PDF
  local f="$TMP/slide_$1"
  echo "<html><head><style>@page { size: 25.4cm 14.29cm; margin: 0.4cm }</style></head><body style='font-family:DejaVu Sans'><table width='100%' cellpadding='30'><tr><td bgcolor='$2' height='300'><h1 style='font-size:40pt;color:white'>$3</h1><p style='font-size:30pt;color:white'>$4</p></td></tr></table></body></html>" > "$f.html"
  soffice --headless --convert-to pdf --outdir "$TMP" "$f.html" >/dev/null 2>&1
  echo "$f.pdf"
}
deck() { # $1 = deck name, rest = one-page PDFs
  local name=$1; shift
  pdfunite "$@" "$TMP/$name.pdf"
}

S1=$(slide s1 "#1f3a93" "Radio Access Network Overview" "Doc 1/1551-FIX 101 Rev A. Base stations, baseband units and antennas.")
S2=$(slide s2 "#c0392b" "Latency Results" "Median latency dropped from 12 ms to 4 ms after the upgrade.")
S2B=$(slide s2b "#c0392b" "Latency Results" "Median latency dropped from 12 ms to 3 ms after the upgrade.")
S3=$(slide s3 "#27ae60" "Energy Savings" "Sleep modes cut site energy use by 25 percent at night.")
S4=$(slide s4 "#8e44ad" "Roadmap 2027" "Open RAN pilots in three markets.")
S5=$(slide s5 "#d35400" "Questions" "Contact the product team for details.")

deck deck_a "$S1" "$S2" "$S3"
deck deck_b "$S1" "$S3" "$S4"
deck deck_a_rev2 "$S1" "$S2B" "$S3"
deck unique_notes "$S5"

cp "$TMP/deck_a.pdf" "$OUT/docs/deck_a.pdf"
cp "$TMP/deck_a.pdf" "$OUT/docs/deck_a (1).pdf"
cp "$TMP/deck_b.pdf" "$OUT/docs/deck_b.pdf"
cp "$TMP/deck_a_rev2.pdf" "$OUT/docs/deck_a_rev2.pdf"

# Departing employee: a downloaded copy of deck_b (duplicate) + own notes (unique).
cp "$TMP/deck_b.pdf" "$OUT/offboarding/jane/deck_b - Copy.pdf"
cp "$TMP/unique_notes.pdf" "$OUT/offboarding/jane/my_notes.pdf"

# Video: each slide of deck_a shown for 8 seconds, 1280x720, with a 440 Hz tone.
pdftoppm -r 60 -png "$TMP/deck_a.pdf" "$TMP/frame"
LIST="$TMP/list.txt"
: > "$LIST"
for img in "$TMP"/frame-*.png; do
  echo "file '$img'" >> "$LIST"
  echo "duration 8" >> "$LIST"
done
last=$(ls "$TMP"/frame-*.png | tail -n 1)
echo "file '$last'" >> "$LIST" # concat demuxer needs the last file repeated
ffmpeg -y -loglevel error -f concat -safe 0 -i "$LIST" \
  -f lavfi -i "sine=frequency=440:duration=24" \
  -vf "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:white,fps=10,format=yuv420p" \
  -c:v libx264 -preset veryfast -crf 28 -c:a aac -shortest "$OUT/video/talk.mp4"

echo "Fixtures written to $OUT:"
find "$OUT" -type f -printf '%10s  %p\n' | sort -k2
