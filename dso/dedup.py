"""`dso dedup` (owner: B). Fills the `duplicates` table (DATA_CONTRACT 7).

1. file_exact: SELECT sha256 FROM files GROUP BY sha256 HAVING COUNT(*) > 1.
2. slide_text: slides in different files with the same non-empty text_sha256.
3. slide_visual: hamming(dhash_a, dhash_b) <= 3 but different text hash.
4. revision: same doc_number, or >= 50% shared slides, but different file sha256.
"""
import re
import difflib
from dso.common.db import connect
from dso.common.hashing import hamming

def run(args):
    conn = connect(args.db)
    
    # Safe re-run: clear old duplicates
    conn.execute("DELETE FROM duplicates")
    
    # ==========================================
    # 1. file_exact (Exact file duplicates)
    # ==========================================
    cursor = conn.execute("""
        SELECT sha256 FROM files GROUP BY sha256 HAVING COUNT(*) > 1
    """)
    exact_hashes = [row[0] for row in cursor.fetchall()]
    
    for sha in exact_hashes:
        # Get all files with this hash
        cursor = conn.execute("SELECT id, name FROM files WHERE sha256 = ? ORDER BY id", (sha,))
        files = cursor.fetchall()
        
        # Find canonical (the one without "(1)", " - Copy", etc.)
        canonical_id = files[0][0]
        for f_id, f_name in files:
            if not re.search(r"\(1\)| - Copy|Copy of", f_name, re.IGNORECASE):
                canonical_id = f_id
                break
                
        # Record duplicates against the canonical file
        for f_id, f_name in files:
            if f_id == canonical_id:
                continue
            
            note = "Name contains copy hint" if re.search(r"\(1\)| - Copy|Copy of", f_name, re.IGNORECASE) else ""
            conn.execute("""
                INSERT INTO duplicates (kind, a_ref, b_ref, note) 
                VALUES ('file_exact', ?, ?, ?)
            """, (str(f_id), str(canonical_id), note))

    # ==========================================
    # 2. slide_text (Same text hash in different files)
    # ==========================================
    cursor = conn.execute("""
        SELECT s1.slide_id, s2.slide_id 
        FROM slides s1
        JOIN slides s2 ON s1.text_sha256 = s2.text_sha256
        WHERE s1.file_id != s2.file_id 
          AND s1.text_sha256 != '' 
          AND s1.slide_id < s2.slide_id
    """)
    for row in cursor.fetchall():
        conn.execute("""
            INSERT INTO duplicates (kind, a_ref, b_ref) 
            VALUES ('slide_text', ?, ?)
        """, (row[0], row[1]))

    # ==========================================
    # 3. slide_visual (Look similar, but text differs)
    # ==========================================
    # To save time, we only compare slides of the same length or from similar files
    cursor = conn.execute("SELECT slide_id, file_id, dhash, text_sha256 FROM slides")
    all_slides = cursor.fetchall()
    
    # Compare every slide to every other slide (N^2, but fast for small datasets)
    for i in range(len(all_slides)):
        for j in range(i + 1, len(all_slides)):
            s1, f1, hash1, text1 = all_slides[i]
            s2, f2, hash2, text2 = all_slides[j]
            
            # Must be different files, and text must be different
            if f1 != f2 and text1 != text2:
                # We use the team's hamming function
                dist = hamming(hash1, hash2)
                if dist <= 3:
                    conn.execute("""
                        INSERT INTO duplicates (kind, a_ref, b_ref, note) 
                        VALUES ('slide_visual', ?, ?, ?)
                    """, (s1, s2, f"Distance: {dist}"))

    # ==========================================
    # 4. revision (Shared decks)
    # ==========================================
    # Find files that share at least 50% of their visual slides with another file
    cursor = conn.execute("""
        SELECT file_id, dhash FROM slides WHERE dhash != ''
    """)
    file_slides = {}
    for f_id, d_hash in cursor.fetchall():
        file_slides.setdefault(f_id, set()).add(d_hash)
        
    file_ids = list(file_slides.keys())
    for i in range(len(file_ids)):
        for j in range(i + 1, len(file_ids)):
            f1, f2 = file_ids[i], file_ids[j]
            set1, set2 = file_slides[f1], file_slides[f2]
            
            # Count exact visual matches
            shared = len(set1.intersection(set2))
            min_slides = min(len(set1), len(set2))
            
            if min_slides > 0 and (shared / min_slides) >= 0.5:
                # Check if they are exact same file (already handled in file_exact)
                cursor = conn.execute("SELECT sha256 FROM files WHERE id IN (?, ?)", (f1, f2))
                shas = [r[0] for r in cursor.fetchall()]
                if len(set(shas)) > 1: # Different file hash
                    conn.execute("""
                        INSERT INTO duplicates (kind, a_ref, b_ref, note) 
                        VALUES ('revision', ?, ?, ?)
                    """, (str(f1), str(f2), f"Shared {shared}/{min_slides} visual slides"))
    conn.commit()
    conn.close()
    print("[dedup] Finished scanning for duplicates.")
    return 0