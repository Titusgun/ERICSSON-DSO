"""`dso docs` (owner: B)

For every file with kind='document' in the DB:
1. If it is not a PDF (pptx, ppt, odp, docx, doc): convert it to
   out/converted/<sha12>.pdf, see convert_to_pdf() (issue #5).
2. For every page: render to image + extract text.
3. text_sha256 = sha256_text(normalize_text(text)); dhash = dhash_image(image)
4. Save image as out/slides/<sha12>/p001.webp
5. slide_id = f"{sha12}:{page_no}:{text_sha256[:8]}" (DATA_CONTRACT 5, v1.1).

One machine, one converter (DATA_CONTRACT v1.1): LibreOffice and PowerPoint render
slides slightly differently, so the final demo uses one machine and one converter.
That is why every conversion prints which converter was used.

Exact duplicate files (same sha256) are processed ONCE: their slides would get
the same slide_ids (slide_id is built from the sha12), and they are identical
anyway; `dso dedup` records the copies as file_exact. The copy whose name has
no copy hint ("(1)", " - Copy", "Copy of") is processed, so the slides belong
to the original, not to "deck (1).pdf".
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pypdfium2 as pdfium

from dso.common.db import connect
from dso.common.hashing import sha256_text, normalize_text, dhash_image, sha12

# DATA_CONTRACT 7: names with these hints are copies.
COPY_HINTS = ["(1)", " - copy", "copy of"]

# Word documents need Word; everything else (pptx, ppt, odp) PowerPoint.
WORD_EXT = ["docx", "doc"]

# A big deck can take a while; a converter that hangs longer is stopped.
CONVERT_TIMEOUT_SEC = 300

# Where LibreOffice is usually installed if `soffice` is not on PATH.
SOFFICE_PLACES = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/bin/soffice",
    "/usr/lib/libreoffice/program/soffice",
    "/snap/bin/libreoffice",
]


# ---------- PPTX / DOCX -> PDF (issue #5) ----------

def app_name(ext):
    """Which Office program opens this file type: "Word" or "PowerPoint"."""
    if ext in WORD_EXT:
        return "Word"
    return "PowerPoint"


def is_good_pdf(path):
    """True if the converter really produced a non-empty file."""
    return path.is_file() and path.stat().st_size > 0


def find_soffice():
    """Path of the LibreOffice program, or None if it is not installed.

    First PATH (soffice / libreoffice), then the standard install folders on
    Windows, macOS and Linux, then /opt/libreoffice*/ (LibreOffice's own .deb/.rpm).
    """
    for program in ["soffice", "libreoffice"]:
        found = shutil.which(program)
        if found is not None:
            return found
    for place in SOFFICE_PLACES:
        if Path(place).is_file():
            return place
    opt = Path("/opt")
    if opt.is_dir():
        for candidate in sorted(opt.glob("libreoffice*/program/soffice")):
            return str(candidate)
    return None


def convert_with_soffice(soffice, source, target):
    """LibreOffice: `soffice --headless --convert-to pdf`. Returns True on success.

    LibreOffice names its output <source name>.pdf, so it writes into its own
    temporary folder and we move the result to `target`. It also gets its own
    settings folder (-env:UserInstallation): otherwise it silently does nothing
    while you have LibreOffice open yourself.
    """
    work_dir = target.parent / ("tmp-" + target.stem)
    profile_dir = (work_dir / "profile").resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    command = [soffice, "-env:UserInstallation=" + profile_dir.as_uri(),
               "--headless", "--convert-to", "pdf", "--outdir", str(work_dir), str(source)]
    ok = False
    try:
        subprocess.run(command, capture_output=True, timeout=CONVERT_TIMEOUT_SEC)
        produced = work_dir / (source.stem + ".pdf")
        if is_good_pdf(produced):
            produced.replace(target)
            ok = True
    except (OSError, subprocess.SubprocessError):
        ok = False
    # Only our own temporary folder inside out/converted/ is removed.
    shutil.rmtree(work_dir, ignore_errors=True)
    return ok


def powershell_quote(path):
    """A path as a PowerShell string: 'C:\\x\\it''s.pptx' (a ' is written twice)."""
    return "'" + str(path).replace("'", "''") + "'"


def convert_with_office_windows(source, target, ext):
    """Windows + Microsoft Office, remote-controlled through COM from PowerShell.

    PowerPoint: Presentations.Open(file, ReadOnly=-1, Untitled=0, WithWindow=0),
    SaveAs(target, 32)   (32 = ppSaveAsPDF).
    Word: Documents.Open(file, ConfirmConversions=$false, ReadOnly=$true),
    SaveAs2(target, 17)  (17 = wdFormatPDF).
    No window opens and the original file is opened read-only.
    UNTESTED on our machines so far - verify on a teammate's Windows laptop.
    """
    if sys.platform != "win32":
        return False
    src = powershell_quote(source.resolve())
    dst = powershell_quote(target.resolve())
    if app_name(ext) == "Word":
        script = ("$app = New-Object -ComObject Word.Application; $app.Visible = $false; "
                  f"try {{ $doc = $app.Documents.Open({src}, $false, $true); "
                  f"$doc.SaveAs2({dst}, 17); $doc.Close($false) }} finally {{ $app.Quit() }}")
    else:
        script = ("$app = New-Object -ComObject PowerPoint.Application; "
                  f"try {{ $deck = $app.Presentations.Open({src}, -1, 0, 0); "
                  f"$deck.SaveAs({dst}, 32); $deck.Close() }} finally {{ $app.Quit() }}")
    command = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
               "-Command", script]
    try:
        subprocess.run(command, capture_output=True, timeout=CONVERT_TIMEOUT_SEC)
    except (OSError, subprocess.SubprocessError):
        return False
    return is_good_pdf(target)


def applescript_quote(path):
    """A path as an AppleScript string: backslashes and quotes escaped."""
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def convert_with_office_mac(source, target, ext):
    """macOS + Microsoft Office, remote-controlled with AppleScript (osascript).

    Best effort: macOS may ask once for permission to control PowerPoint/Word
    or to access the folder. UNTESTED on our machines so far - verify on a
    teammate's Mac.
    """
    if sys.platform != "darwin":
        return False
    src = applescript_quote(source.resolve())
    dst = applescript_quote(target.resolve())
    if app_name(ext) == "Word":
        lines = ['tell application "Microsoft Word"',
                 f"open POSIX file {src}",
                 "set theDoc to active document",
                 f"save as theDoc file name {dst} file format format PDF",
                 "close theDoc saving no",
                 "end tell"]
    else:
        lines = ['tell application "Microsoft PowerPoint"',
                 f"open POSIX file {src}",
                 "set theDeck to active presentation",
                 f"save theDeck in POSIX file {dst} as save as PDF",
                 "close theDeck saving no",
                 "end tell"]
    command = ["osascript"]
    for line in lines:
        command = command + ["-e", line]       # one -e per line of the script
    try:
        subprocess.run(command, capture_output=True, timeout=CONVERT_TIMEOUT_SEC)
    except (OSError, subprocess.SubprocessError):
        return False
    return is_good_pdf(target)


def convert_to_pdf(file_path, name, file_sha, out_dir):
    """Returns the path of a PDF version of a PPTX/DOCX file, or None.

    Tries, in this order (issue #5), and prints which one worked:
      1. out/converted/<sha12>.pdf already exists -> use it (also a manual export)
      2. LibreOffice
      3. Windows: PowerPoint / Word through PowerShell + COM
      4. macOS:   PowerPoint / Word through AppleScript
      5. nothing worked -> print the exact manual step, return None
    """
    ext = file_path.suffix.lower().lstrip(".")
    target = out_dir / "converted" / f"{sha12(file_sha)}.pdf"
    if is_good_pdf(target):
        print(f"[docs]   using {target.as_posix()} (already converted)")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)

    soffice = find_soffice()
    if soffice is not None:
        print(f"[docs]   converting with LibreOffice ({soffice}) ...")
        if convert_with_soffice(soffice, file_path, target):
            print("[docs]   converter used: LibreOffice")
            return target
    if convert_with_office_windows(file_path, target, ext):
        print(f"[docs]   converter used: {app_name(ext)} (Windows)")
        return target
    if convert_with_office_mac(file_path, target, ext):
        print(f"[docs]   converter used: {app_name(ext)} (macOS)")
        return target

    print(f"[docs]   could not convert {name} - no working converter found.")
    print(f"[docs]   Manual step: Open {file_path.as_posix()} in {app_name(ext)}, export as PDF, "
          f"save it as {target.as_posix()}, then run `dso docs` again.")
    return None


def has_copy_hint(name):
    """True for names like "deck (1).pdf", "deck - Copy.pdf", "Copy of deck.pdf"."""
    lower = name.lower()
    for hint in COPY_HINTS:
        if hint in lower:
            return True
    return False


def clear_old_slides(conn, file_id):
    """Removes the slides of an earlier run of this file (re-run safe).

    frames.slide_id points into slides (set by `dso link`), and SQLite checks
    that link (PRAGMA foreign_keys = ON). So the frames are unlinked first;
    `dso link` sets them again.
    """
    conn.execute(
        """UPDATE frames SET slide_id = NULL, match_dist = NULL
           WHERE slide_id IN (SELECT slide_id FROM slides WHERE file_id = ?)""", (file_id,))
    conn.execute("DELETE FROM slides WHERE file_id = ?", (file_id,))


def run(args):
    conn = connect(args.db)

    # Fetch all documents that were ingested
    cursor = conn.execute("SELECT id, path, name, sha256, doc_number FROM files WHERE kind='document'")
    rows = cursor.fetchall()

    # Originals first, copies ("(1)", " - Copy") after them, so that for exact
    # duplicates the original is the one that gets processed.
    files = []
    for row in rows:
        if not has_copy_hint(row["name"]):
            files.append(row)
    for row in rows:
        if has_copy_hint(row["name"]):
            files.append(row)

    if not files:
        print("[docs] No documents found. Run 'dso ingest <folder>' first.")
        return 0

    # Ensure output directory exists
    out_dir = Path(args.out)
    slides_dir = out_dir / "slides"
    slides_dir.mkdir(parents=True, exist_ok=True)

    total_slides = 0
    done_hashes = set()     # sha256 of documents already processed in this run

    for row in files:
        file_id = row[0]
        path_str = row[1]
        name = row[2]
        file_sha = row[3]

        if file_sha in done_hashes:
            # Exact copy of a document we already did: no slides of its own.
            # Also remove slides an older version of this code made for it.
            clear_old_slides(conn, file_id)
            print(f"[docs] {name}: exact duplicate, slides already done - skipped")
            continue

        file_path = Path(path_str)
        if not file_path.exists():
            print(f"[docs] File missing, skipping: {path_str}")
            continue

        # --- 1. Convert to PDF if necessary ---
        pdf_path = file_path
        if not path_str.lower().endswith(".pdf"):
            print(f"[docs] {name}: not a PDF, converting")
            pdf_path = convert_to_pdf(file_path, name, file_sha, out_dir)
            if pdf_path is None:
                continue                # skipped; the manual step was printed

        # --- 2. Open PDF ---
        try:
            pdf = pdfium.PdfDocument(str(pdf_path))
        except Exception as e:
            print(f"[docs] Failed to open PDF {name}: {e}")
            continue

        # Safe re-run: delete existing slides for this file
        clear_old_slides(conn, file_id)

        # The 'doc' part of the slide_id is always the file's sha12 (DATA_CONTRACT
        # 5, v1.1); a doc_number could be shared by two revisions -> same slide_ids.
        doc_ref = sha12(file_sha)
        # --- 3. Process every page ---
        for page_idx in range(len(pdf)):
            page_no = page_idx + 1 # DATA CONTRACT requires 1-based page numbers
            page = pdf[page_idx]
            
            # Extract image and text
            image = page.render(scale=1).to_pil()
            raw_text = page.get_textpage().get_text_bounded()
            # Store the ORIGINAL text (shown in search, web UI, export); only the
            # hash uses the normalized (lowercase, single spaces) version.
            # pdfium ends lines with "\r\n"; keep plain "\n" on every OS.
            display_text = raw_text.replace("\r\n", "\n").strip()

            # Hash them using shared functions
            normalized = normalize_text(raw_text)
            text_hash = sha256_text(normalized)
            visual_hash = dhash_image(image)
            
            # Create slide_id
            slide_id = f"{doc_ref}:{page_no}:{text_hash[:8]}"
            
            # Save image to out/slides/<sha12>/p001.webp
            slide_img_dir = slides_dir / sha12(file_sha)
            slide_img_dir.mkdir(exist_ok=True)
            img_filename = f"p{page_no:03d}.webp"
            img_path = slide_img_dir / img_filename
            image.save(img_path, "WEBP")
            
            # Relative path for the DB (e.g., slides/3fa9c0d1e2b4/p001.webp)
            rel_img_path = img_path.relative_to(out_dir).as_posix()

            # Insert into DB
            conn.execute("""
                INSERT INTO slides (
                    slide_id, file_id, page_no, text, text_sha256, dhash, image_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                slide_id, file_id, page_no, display_text, text_hash, visual_hash, rel_img_path
            ))

            total_slides += 1

        # Close the PDF: on Windows an open file cannot be deleted or replaced.
        pdf.close()
        done_hashes.add(file_sha)

    conn.commit()
    conn.close()
    print(f"[docs] Processed {len(files)} documents. Generated {total_slides} slides.")
    return 0