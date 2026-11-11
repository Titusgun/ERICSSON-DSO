"""Hashing helpers. SHARED FILE - see docs/DATA_CONTRACT.md section 4.

Everyone uses these functions, so frames and slides are hashed exactly the same way.
"""
import hashlib

import imagehash
from PIL import Image


def sha256_file(path):
    """SHA-256 of a whole file as 64 lowercase hex chars.

    Reads 1 MB at a time, so a 2 GB video does not need to fit in memory.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        chunk = f.read(1024 * 1024)
        while chunk:
            h.update(chunk)
            chunk = f.read(1024 * 1024)
    return h.hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha12(full_hash):
    """Short ID used for folder names: first 12 hex chars of a SHA-256."""
    return full_hash[:12]


def normalize_text(text):
    """Lowercase, collapse all whitespace to single spaces, trim both ends."""
    return " ".join(text.lower().split())


def dhash_image(image):
    """64-bit difference hash of a PIL image, as 16 lowercase hex chars.

    imagehash shrinks the image to 9x8 grey pixels and compares each pixel with
    its right neighbour: 8 rows x 8 comparisons = 64 bits. Similar images give
    similar bits.
    """
    return str(imagehash.dhash(image, hash_size=8))


def dhash_file(path):
    with Image.open(path) as img:
        return dhash_image(img)


def hamming(hex_a, hex_b):
    """Number of differing bits between two dHash hex strings (0..64)."""
    a = imagehash.hex_to_hash(hex_a)
    b = imagehash.hex_to_hash(hex_b)
    return int(a - b)
