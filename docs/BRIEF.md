# Challenge brief (summary) — Ericsson "Data Storage Optimization"

Campor × BUILD Hackathon 2026 · 13–15 November · Black Box, Ericsson, Karlskrona.
This is our summary of the official brief. When in doubt, the official PDF wins;
ask the organizers/coaches about anything unclear.

## Goal
A tool that frees up storage without losing information: turn long recordings and
documents into compact, searchable, AI-ready data, and find duplicates across systems
(SharePoint, OneDrive, Ericsson Play). Partial solutions are explicitly fine.

Two issues:
- **Storage reduction**: replace content such as video with text and key frames; identify duplicates.
- **Data activation**: make content searchable and consumable by AI.

Also: when someone leaves the company, analyse their files, compare with existing
copies, and delete only if a duplicate already exists.

## Final pitch must present
1. Which use cases we addressed.
2. How we solved them technically.
3. Scenario-based percentage savings in data volume.
4. A future-proof data strategy supporting a data-driven AI approach.
5. Areas for further work and unresolved problems.

Reference scenario from the brief: a 2-hour recording (~1 GB) → ~50 KB text + ~2 MB
slide frames ≈ 99% reduction.

## Suggested use cases
Recording text: plain text; with each speaker identified.

Slide frame from video, increasing complexity: screenshot → compressed → identified
by document number + slide number → linked to a single source of truth → described
as a text prompt so it can be recreated.

Also: event compressed to ASCII text, image, unique slide ID or voice sample;
text time-linked to the frame ("as you see here" still makes sense); search returns
frame + text; can the recording be recreated as a video?

Documents: identify and handle duplicates; break PDFs/PowerPoints into slides with a
hash per slide (optionally document number + file name); handle presentations with
several revisions with minor slide changes.

## Sample material (provided at the event)
MP4 recordings of an event; the PDFs presented; duplicates with a "(1).pdf" suffix;
presentations sharing some slides; metadata (document number, revision, author, date).
