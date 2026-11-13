"""`dso docs` (owner: B)

For every file with kind='document' in the DB:
1. If it is not a PDF (pptx, docx, odp): convert with LibreOffice if it is installed.
2. For every page: render to image + extract text.
3. text_sha256 = sha256_text(normalize_text(text)); dhash = dhash_image(image)
4. Save image as out/slides/<sha12>/p001.webp
5. slide_id = f"{doc}:{page_no}:{text_sha256[:8]}" (DATA_CONTRACT 5).
"""
import subprocess
from pathlib import Path

import pypdfium2 as pdfium

from dso.common.db import connect
from dso.common.hashing import sha256_text, normalize_text, dhash_image, sha12

def run(args):
    conn = connect(args.db)
    
    # Fetch all documents that were ingested
    cursor = conn.execute("SELECT id, path, name, sha256, doc_number FROM files WHERE kind='document'")
    files = cursor.fetchall()
    
    if not files:
        print("[docs] No documents found. Run 'dso ingest <folder>' first.")
        return 0

    # Ensure output directory exists
    out_dir = Path(args.out)
    slides_dir = out_dir / "slides"
    tmp_dir = out_dir / "tmp"
    slides_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    total_slides = 0

    for row in files:
        file_id = row[0]
        path_str = row[1]
        name = row[2]
        file_sha = row[3]
        doc_number = row[4]
        
        file_path = Path(path_str)
        if not file_path.exists():
            print(f"[docs] File missing, skipping: {path_str}")
            continue

        # --- 1. Convert to PDF if necessary ---
        pdf_path = file_path
        if not path_str.lower().endswith(".pdf"):
            print(f"[docs] Converting {name} to PDF via LibreOffice...")
            try:
                # Windows default installation path for LibreOffice
                soffice_path = r"C:\Program Files\LibreOffice\program\soffice.exe"
                subprocess.run([
                    soffice_path, "--headless", "--convert-to", "pdf",
                    "--outdir", str(tmp_dir), str(file_path)
                ], check=True, capture_output=True)
                pdf_path = tmp_dir / (file_path.stem + ".pdf")
            except Exception as e:
                print(f"[docs] Warning: Could not convert {name}. Skipping. (Error: {e})")
                continue

        # --- 2. Open PDF ---
        try:
            pdf = pdfium.PdfDocument(str(pdf_path))
        except Exception as e:
            print(f"[docs] Failed to open PDF {name}: {e}")
            continue

        # Safe re-run: delete existing slides for this file
        conn.execute("DELETE FROM slides WHERE file_id = ?", (file_id,))

        # Determine the 'doc' part of the slide_id
        # DATA CONTRACT: <doc> = files.doc_number if known, else the file's sha12
        doc_ref = doc_number if doc_number else f"{sha12(file_sha)}_{file_id}"
        # --- 3. Process every page ---
        for page_idx in range(len(pdf)):
            page_no = page_idx + 1 # DATA CONTRACT requires 1-based page numbers
            page = pdf[page_idx]
            
            # Extract image and text
            image = page.render(scale=1).to_pil()
            raw_text = page.get_textpage().get_text_bounded()
            
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
                slide_id, file_id, page_no, normalized, text_hash, visual_hash, rel_img_path
            ))
            
            total_slides += 1

    conn.commit()
    conn.close()
    print(f"[docs] Processed {len(files)} documents. Generated {total_slides} slides.")
    return 0