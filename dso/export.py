"""`dso export` (owner: C)

For every video and document write out/export/<sha12>.json in the format of
DATA_CONTRACT 8 (json.dump(data, f, indent=2, ensure_ascii=False)) and a readable
out/export/<sha12>.md: one heading per frame with timestamp, image link and the
transcript under it.
"""

import os
import sqlite3
import pypdfium2 as pdfium
import subprocess


def run(args):
    # 1. Connect to the database
    # (Need to know how your team connects to the DB)
    
    # 2. Fetch all files where kind='document'
    # "SELECT id, path FROM files WHERE kind='document'"
    
    # 3. Loop through each file:
    for file in files:
        file_path = file["path"]
        
        # Check if it's a PDF. If not, try converting with LibreOffice.
        if not file_path.lower().endswith(".pdf"):
            # LibreOffice conversion logic here...
            pass
            
        # 4. Open the PDF and read metadata
        pdf = pdfium.PdfDocument(file_path)
        metadata = pdf.get_metadata_dict()
        # Update author/title in DB if needed...
        
        # 5. Re-run safety: Delete old slides for this file
        # "DELETE FROM slides WHERE file_id = ?"
        
        # 6. Loop through each page (slide)
        for i in range(len(pdf)):
            page = pdf[i]
            
            # Render image and extract text
            image = page.render(scale=1).to_pil()
            text = page.get_textpage().get_text_bounded()
            
            # Hash them
            text_hash = sha256_text(normalize_text(text))
            img_hash = dhash_image(image)
            
            # Create slide_id
            slide_id = f"{file['id']}:{i}:{text_hash[:8]}"
            
            # Save the image to out/slides/<hash>/p001.webp
            # (We need to make the directory first)
            
            # Insert into database!
            # "INSERT INTO slides (...) VALUES (...)"
            
    print("Done processing documents!")
    return 0