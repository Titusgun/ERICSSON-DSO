"""`dso dedup` (owner: B). Fills the `duplicates` table (DATA_CONTRACT 7).

1. file_exact: SELECT sha256 FROM files GROUP BY sha256 HAVING COUNT(*) > 1.
2. slide_text: slides in different files with the same non-empty text_sha256.
3. slide_visual: hamming(dhash_a, dhash_b) <= 3 but different text hash.
4. file_content / revision between two documents with different file bytes
   (issue #14, DATA_CONTRACT 6 + 7), see compare_documents():
   - file_content: every page has the same text, e.g. the same deck saved twice
   - revision: same doc_number, or >= 50% shared slides, AND text similarity
     > 0.8. Two decks that only share a few slides are not revisions.
   For both, b_ref = the NEWEST document (the one to keep) and the note lists
   which slides are identical, changed, added or removed.

   Slides are compared by WHICH WORDS they contain, in any order (plus "looks
   the same" by dHash). On the real "final" / "final2" decks, LibreOffice put the
   text boxes of identical-looking slides in a different order, so comparing
   the text as one string found only 1 of 22 slides the same (#14).
"""
import re
from dso.common.db import connect
from dso.common.hashing import hamming, normalize_text, sha256_text

# A slide without text (only a picture) still has a text hash: the hash of "".
# All such slides share it, so they must not count as "same text".
EMPTY_TEXT_SHA256 = sha256_text(normalize_text(""))

# DATA_CONTRACT 7: candidates share at least half of the slides (of the smaller deck).
REVISION_MIN_SHARED = 0.5
# DATA_CONTRACT 6: and the text is more than 80 % the same. Measured as the
# average, over the smaller deck's slides, of how well each slide's words match
# its best partner in the other deck (order ignored). Measured 13 Nov:
#   fixtures deck_a / deck_a_rev2 = 0.95 -> revision
#   real "final" / "final2" (22 of 25 slides kept) >= 0.89 -> revision
#   fixtures deck_a / deck_b (share 2 of 3 slides) = 0.67 -> not a revision
REVISION_MIN_SIMILARITY = 0.8

# Two slides are "the same slide" (maybe edited) if their word sets overlap this
# much, or if they look the same (dHash distance <= SLIDE_VISUAL_MAX).
SLIDE_MATCH_MIN = 0.8
SLIDE_VISUAL_MAX = 3
# Below this word overlap a new slide is "added", not a changed version of an old one.
SLIDE_CHANGED_MIN = 0.5


# ---------- 4. file_content / revision helpers ----------

def slide_words(text):
    """The set of words on a slide, lowercase; order and punctuation ignored."""
    return set(re.findall(r"\w+", text.lower()))


def word_overlap(words_a, words_b):
    """How many words two slides share: 1.0 = same words, 0.0 = none (Jaccard)."""
    if len(words_a) == 0 and len(words_b) == 0:
        return 1.0
    return len(words_a & words_b) / len(words_a | words_b)


def load_documents(conn):
    """Every document that has slides, as a list of dicts:
    {"id", "name", "sha256", "doc_number", "revision", "doc_date",
     "pages": [text_sha256 of page 1, page 2, ...],
     "slides": [{"words": set of words, "dhash": ...} for page 1, page 2, ...]}
    """
    documents = []
    files = conn.execute(
        """SELECT id, name, sha256, doc_number, revision, doc_date FROM files
           WHERE id IN (SELECT DISTINCT file_id FROM slides) ORDER BY id""").fetchall()
    for f in files:
        pages = []
        slides = []
        for slide in conn.execute(
                "SELECT text, text_sha256, dhash FROM slides WHERE file_id = ? ORDER BY page_no", (f["id"],)):
            pages.append(slide["text_sha256"])
            slides.append({"words": slide_words(slide["text"]), "dhash": slide["dhash"]})
        documents.append({
            "id": f["id"], "name": f["name"], "sha256": f["sha256"],
            "doc_number": f["doc_number"], "revision": f["revision"], "doc_date": f["doc_date"],
            "pages": pages, "slides": slides,
        })
    return documents


def best_partner(slide, candidates, used):
    """The candidate slide most like `slide`. Returns (index, word overlap,
    looks_same), or (None, 0.0, False). Indexes in `used` are skipped."""
    best_index = None
    best_overlap = -1.0
    best_looks_same = False
    for index in range(len(candidates)):
        if index in used:
            continue
        other = candidates[index]
        overlap = word_overlap(slide["words"], other["words"])
        looks_same = hamming(slide["dhash"], other["dhash"]) <= SLIDE_VISUAL_MAX
        # A slide that looks the same wins a tie; otherwise more shared words wins.
        if overlap > best_overlap or (overlap == best_overlap and looks_same and not best_looks_same):
            best_index = index
            best_overlap = overlap
            best_looks_same = looks_same
    if best_index is None:
        return None, 0.0, False
    return best_index, best_overlap, best_looks_same


def deck_similarity(a, b):
    """Returns (shared, similarity), both 0.0 - 1.0, for the smaller deck:
    shared     = share of its slides that have a matching slide in the other deck
    similarity = average word overlap of each of its slides with its best partner
    Slide ORDER does not matter, and slides only in the bigger deck do not lower
    the score (a revision that deletes slides is still a revision)."""
    small = a["slides"]
    big = b["slides"]
    if len(small) > len(big):
        small, big = big, small
    if len(small) == 0:
        return 0.0, 0.0
    matched = 0
    overlap_sum = 0.0
    for slide in small:
        index, overlap, looks_same = best_partner(slide, big, set())
        if overlap >= SLIDE_MATCH_MIN or looks_same:
            matched += 1
        overlap_sum += overlap
    return matched / len(small), overlap_sum / len(small)


def sortable(text):
    """Text that sorts numbers by value: "final2" < "final10" (zero-padded digits)."""
    parts = re.split(r"(\d+)", text.lower())
    result = ""
    for part in parts:
        if part.isdigit():
            result = result + part.zfill(8)
        else:
            result = result + part
    return result


def older_and_newer(a, b):
    """Returns (older, newer, reason). The newer one is kept (canonical, b_ref).

    DATA_CONTRACT 7: highest revision / newest date. Fallbacks when that is
    unknown: the file name ("final2" is newer than "final"), then ingest order.
    """
    checks = [
        ("revision", a["revision"], b["revision"]),
        ("date", a["doc_date"], b["doc_date"]),
        ("file name", a["name"].rsplit(".", 1)[0], b["name"].rsplit(".", 1)[0]),
    ]
    for reason, value_a, value_b in checks:
        if value_a and value_b and value_a != value_b:
            if sortable(value_a) < sortable(value_b):
                return a, b, reason
            return b, a, reason
    if a["id"] < b["id"]:
        return a, b, "ingest order"
    return b, a, "ingest order"


def page_list(numbers):
    """[3, 7] -> "3, 7"; [] -> "none"."""
    if len(numbers) == 0:
        return "none"
    texts = []
    for n in numbers:
        texts.append(str(n))
    return ", ".join(texts)


def is_real_word_change(only_here, words_there):
    """True if one of the words in `only_here` is a real word change.

    NOT real (minor): digits ("4" -> "5", slide numbers, dates, values), single
    letters, and fragments of a word on the other side ("final" vs "final2",
    "orches" + "tration"). Measured on the real "final" / "final2" decks:
    every slide differed only in such tokens (slide numbers and the footer).
    """
    for word in only_here:
        if word.isdigit() or len(word) == 1:
            continue
        fragment = False
        for other in words_there:
            if word in other or other in word:
                fragment = True
                break
        if not fragment:
            return True
    return False


def slide_changes(older, newer):
    """Which slides stayed the same, changed, were added or removed.

    Every slide of the NEWER deck is paired with its most similar unused slide of
    the older deck (order ignored, so moved or inserted slides do not make all
    later slides "changed"):
      same words                                  -> identical
      only numbers / single letters / word parts  -> minor (e.g. slide number,
                                                     footer, "4 ms" -> "3 ms")
      at least one real word differs              -> changed
      hardly any words in common                  -> added (new slide)
    Old slides that nobody picked were removed.
    Returns (identical_count, minor, changed, added, removed): minor, changed and
    added are page numbers in the NEWER deck, removed are pages in the OLDER deck.
    """
    identical = 0
    minor = []
    changed = []
    added = []
    used = set()            # older slides already paired
    for new_index in range(len(newer["slides"])):
        slide = newer["slides"][new_index]
        old_index, overlap, looks_same = best_partner(slide, older["slides"], used)
        page = new_index + 1                        # pages are 1-based
        if old_index is None or (overlap < SLIDE_CHANGED_MIN and not looks_same):
            added.append(page)
            continue
        used.add(old_index)
        new_words = slide["words"]
        old_words = older["slides"][old_index]["words"]
        if new_words == old_words:
            identical += 1
        elif (is_real_word_change(new_words - old_words, old_words)
              or is_real_word_change(old_words - new_words, new_words)):
            changed.append(page)
        else:
            minor.append(page)
    removed = []
    for old_index in range(len(older["slides"])):
        if old_index not in used:
            removed.append(old_index + 1)
    return identical, minor, changed, added, removed


def compare_documents(a, b):
    """Returns (kind, older, newer, note) for two documents, or None if unrelated.
    kind is "file_content" or "revision" (DATA_CONTRACT 7)."""
    if a["sha256"] == b["sha256"] or len(a["pages"]) == 0 or len(b["pages"]) == 0:
        return None                   # exact copies are file_exact; empty docs say nothing
    older, newer, reason = older_and_newer(a, b)
    newest = f"newest: {newer['name']} (by {reason})"

    if a["pages"] == b["pages"]:
        note = f"all {len(a['pages'])} slides identical, file bytes differ; {newest}"
        return "file_content", older, newer, note

    same_number = a["doc_number"] is not None and a["doc_number"] == b["doc_number"]
    shared, similarity = deck_similarity(a, b)
    if not same_number and shared < REVISION_MIN_SHARED:
        return None
    if not same_number and similarity <= REVISION_MIN_SIMILARITY:
        return None                   # they share slides, but are different decks

    identical, minor, changed, added, removed = slide_changes(older, newer)
    note = (f"text similarity {similarity:.2f}; {identical} of {len(newer['pages'])} slides identical; "
            f"minor (numbers/footer only): {page_list(minor)}; changed text: {page_list(changed)}; "
            f"added: {page_list(added)}; removed: {page_list(removed)}; {newest}")
    return "revision", older, newer, note

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
          AND s1.text_sha256 != ?
          AND s1.slide_id < s2.slide_id
    """, (EMPTY_TEXT_SHA256,))
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
                    # DATA_CONTRACT 7: the Hamming distance goes into `distance`.
                    conn.execute("""
                        INSERT INTO duplicates (kind, a_ref, b_ref, distance)
                        VALUES ('slide_visual', ?, ?, ?)
                    """, (s1, s2, dist))

    # ==========================================
    # 4. file_content / revision (whole documents, issue #14)
    # ==========================================
    documents = load_documents(conn)
    related = 0
    for i in range(len(documents)):
        for j in range(i + 1, len(documents)):
            result = compare_documents(documents[i], documents[j])
            if result is None:
                continue
            kind, older, newer, note = result
            # a_ref = the older one, b_ref = the newest = canonical (keep this one).
            conn.execute("""
                INSERT INTO duplicates (kind, a_ref, b_ref, note)
                VALUES (?, ?, ?, ?)
            """, (kind, str(older["id"]), str(newer["id"]), note))
            related += 1
            # The report asked for in #6: which slides are identical and which differ.
            print(f"[dedup] {kind}: {older['name']} -> {newer['name']}")
            print(f"[dedup]   {note}")

    conn.commit()
    counts = {}
    for row in conn.execute("SELECT kind, COUNT(*) FROM duplicates GROUP BY kind"):
        counts[row[0]] = row[1]
    conn.close()
    print(f"[dedup] done: {counts if counts else 'no duplicates'}")
    return 0