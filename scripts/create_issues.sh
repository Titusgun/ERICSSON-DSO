#!/usr/bin/env bash
# Creates labels and the starter issues on GitHub. Run once after the repo exists.
# Needs the GitHub CLI logged in (`gh auth login`). On Windows run it in Git Bash.
set -euo pipefail
cd "$(dirname "$0")/.."

label() { gh label create "$1" --color "$2" --force > /dev/null; }
label "owner:A" "1f77b4"; label "owner:B" "ff7f0e"; label "owner:C" "2ca02c"; label "owner:D" "9467bd"
label "MUST" "d62728"; label "SHOULD" "bcbd22"; label "COULD" "c7c7c7"; label "contract" "000000"

issue() { gh issue create --title "$1" --label "$2" --body "$3" > /dev/null; echo "created: $1"; }

issue "Contract review: DATA_CONTRACT v1 (first hour, all 4)" "contract,MUST" \
"Read docs/DATA_CONTRACT.md + dso/common/schema.sql together. Change anything now in ONE PR. After this only \`contract:\` PRs."

issue "Real data inventory (first hour, D)" "owner:D,MUST" \
"Copy Ericsson samples into data/ (git-ignored) on every laptop. List file types, sizes, total GB, video lengths, any PPTX. Ask organizers what the jury wants to see. Post the list in chat."

issue "video: key frames (1 fps + dHash)" "owner:A,MUST" \
"See docstring in dso/video.py. Un-skip test_key_frames (3 frames at 0/8/16 s)."

issue "video: transcript with faster-whisper" "owner:A,MUST" \
"segments table with timestamps. Fixture audio is a tone; test on a real recording with tiny.en first, then base.en. Start long real recordings early - CPU transcription takes a while."

issue "docs: PDF -> slides (text hash + dHash + slide_id)" "owner:B,MUST" \
"pypdfium2 per page. Un-skip test_slides (16 slides)."

issue "dedup: exact + slide-level duplicates" "owner:B,MUST" \
"Un-skip test_duplicates. Canonical copy rules in DATA_CONTRACT 7."

issue "index + search (FTS5)" "owner:C,MUST" \
"search() returns DATA_CONTRACT 9 dicts; CLI and web use the same function. Un-skip test_search."

issue "link: segment -> frame -> slide" "owner:C,MUST" \
"Un-skip test_frames_linked_to_slides."

issue "savings report" "owner:D,MUST" \
"video_distill, dedup, offboarding scenarios with % on REAL data. Un-skip test_savings."

issue "Tune thresholds on real data (A + B)" "owner:B,MUST" \
"Use \`uv run dso dhash a.webp b.webp\` on real frames/slides. Update DATA_CONTRACT 6 + DECISIONS.md."

issue "export: AI-ready JSON + Markdown" "owner:C,SHOULD" "DATA_CONTRACT 8."
issue "offboard: dry-run report" "owner:D,SHOULD" "Jane fixture: copy -> safe to delete, my_notes -> keep. Never deletes."
issue "web: search results with frame + jump to video time" "owner:D,SHOULD" \
"Result card shows frame image + snippet; link plays the original video at t_sec (<video src=...#t=96>)."
issue "revisions: deck_a_rev2 = revision of deck_a" "owner:B,SHOULD" "Un-skip test_revision. Text-based, see DATA_CONTRACT 6."
issue "Pitch deck with our measured numbers" "owner:D,MUST" "5 points from docs/BRIEF.md. Start Saturday evening at the latest."
