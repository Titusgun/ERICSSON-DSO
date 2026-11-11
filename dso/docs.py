"""`dso docs` (owner: B)

For every file with kind='document' in the DB:

1. If it is not a PDF (pptx, docx, odp): convert with LibreOffice if it is installed
       soffice --headless --convert-to pdf --outdir out/tmp <file>
   (Windows: "C:/Program Files/LibreOffice/program/soffice.exe"). If LibreOffice is
   missing, skip the file with a warning - the samples are mostly PDFs.
2. For every page:
       import pypdfium2 as pdfium
       pdf = pdfium.PdfDocument(path)
       page = pdf[i]
       image = page.render(scale=1).to_pil()           # picture of the slide
       text = page.get_textpage().get_text_bounded()   # text on the slide
3. text_sha256 = sha256_text(normalize_text(text)); dhash = dhash_image(image)
4. Save image as out/slides/<sha12>/p001.webp
5. slide_id = f"{doc}:{page_no}:{text_sha256[:8]}" (DATA_CONTRACT 5). Insert into `slides`.
6. Metadata: pdf.get_metadata_dict() gives Title/Author/... -> update files.author etc.
   Ericsson document numbers may also be in the text - agree a regex after seeing samples.

Fixture check: 16 slides in total (4 decks x 3 + jane's 3 + 1).
Re-run safe: DELETE FROM slides WHERE file_id = ? before inserting.
"""


def run(args):
    print("[docs] TODO (owner B): PDF -> slides with text hash + dHash")
    return 0
