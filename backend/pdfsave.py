"""Serialise a PyMuPDF document quickly.

Document.tobytes() streams its output into Python in millions of tiny writes: 0.20 s for a 2 MB,
33-page Chrome PDF, against 0.016 s for Document.save() to a file and reading it back. And
garbage=4 (de-duplicate every stream) costs another 1.4 s on that file. The edit engines saved the
whole document that way after every intermediate step — 13 times for 4 edits, 16.5 s of a 21 s
request. Intermediate saves only need unreferenced objects dropped (garbage=1).
"""
import os
import tempfile

_DIR = None


def _tmpdir() -> str:
    global _DIR
    if _DIR is None:
        _DIR = tempfile.gettempdir()
    return _DIR


def to_bytes(doc, **save_options) -> bytes:
    """doc's bytes, written through a temporary file; falls back to doc.tobytes on any error."""
    fd, path = tempfile.mkstemp(suffix=".pdf", dir=_tmpdir())
    os.close(fd)
    try:
        doc.save(path, **save_options)
        with open(path, "rb") as f:
            return f.read()
    except Exception:  # noqa: BLE001 — e.g. a read-only temp dir: the slow path still works
        return doc.tobytes(**save_options)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
