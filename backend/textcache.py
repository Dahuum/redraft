"""Reuse page text extraction across the many times one edit request reads the same page.

One /edit of 7 fields on a one-page attestation called Page.get_text 188 times, 132 of them on a
page already read in exactly the same state, and those reads were 65% of the request. The engines
open the document from bytes again and again (152 times in that request), so the cache is keyed on
the bytes a document was opened from, the page number and the arguments.

Safe by construction:
  * only documents opened from in-memory bytes are cached, and only while they have no unsaved
    change (Document.is_dirty — set by update_stream, insert_text, redaction, xref edits, ...);
    a modified document is read fresh every time;
  * every hit returns a fresh copy, so a caller that mutates the result cannot affect the next one;
  * a call with its own TextPage, or with an argument the key can't represent, is not cached.

Page.get_fonts is reused the same way. It walks the page's resources recursively, and one
3-edit request on a 15-page arXiv paper listed fonts 865 times (1.1 s of 5.2 s), almost all on
documents not yet changed: the in-place engine looks fonts up page by page from a dozen places.

install() is idempotent.
"""
import hashlib
import pickle
import threading
from collections import OrderedDict

import fitz

_LOCK = threading.Lock()
_ENTRIES: "OrderedDict[tuple, bytes]" = OrderedDict()
_SIZE = [0]
_FONTS: "OrderedDict[tuple, tuple]" = OrderedDict()
_MAX_BYTES = 64 * 1024 * 1024
_HASHES: "OrderedDict[int, tuple]" = OrderedDict()   # id(source bytes) -> (source bytes, digest)
_STATS = {"hit": 0, "miss": 0, "skip": 0, "font_hit": 0, "font_miss": 0, "font_skip": 0}
_installed = [False]


def _digest(src) -> str:
    with _LOCK:
        hit = _HASHES.get(id(src))
        if hit is not None and hit[0] is src:
            _HASHES.move_to_end(id(src))
            return hit[1]
    d = hashlib.sha1(src).hexdigest()
    with _LOCK:
        _HASHES[id(src)] = (src, d)
        while len(_HASHES) > 64:
            _HASHES.popitem(last=False)
    return d


def _arg_key(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (fitz.Rect, fitz.IRect, tuple, list)):
        return tuple(float(x) for x in v)
    raise TypeError


def install() -> None:
    if _installed[0]:
        return
    _installed[0] = True
    orig_init = fitz.Document.__init__
    orig_get_text = fitz.Page.get_text
    orig_get_fonts = fitz.Page.get_fonts

    def init(self, filename=None, stream=None, *args, **kwargs):
        orig_init(self, filename, stream, *args, **kwargs)
        src = stream if stream is not None else (filename if isinstance(filename, (bytes, bytearray)) else None)
        if isinstance(src, bytearray):
            src = None            # mutable: its content can change under the same object
        try:
            self._rd_src = src
        except Exception:  # noqa: BLE001
            pass

    def get_text(self, option="text", *args, **kwargs):
        doc = self.parent
        src = getattr(doc, "_rd_src", None)
        if src is None or "textpage" in kwargs or doc.is_dirty:
            _STATS["skip"] += 1
            return orig_get_text(self, option, *args, **kwargs)
        try:
            key = (_digest(src), self.number, option,
                   tuple(_arg_key(a) for a in args),
                   tuple(sorted((k, _arg_key(v)) for k, v in kwargs.items())))
        except TypeError:
            _STATS["skip"] += 1
            return orig_get_text(self, option, *args, **kwargs)
        with _LOCK:
            blob = _ENTRIES.get(key)
            if blob is not None:
                _ENTRIES.move_to_end(key)
        if blob is not None:
            _STATS["hit"] += 1
            return pickle.loads(blob)
        _STATS["miss"] += 1
        out = orig_get_text(self, option, *args, **kwargs)
        try:
            blob = pickle.dumps(out, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception:  # noqa: BLE001 — not storable; serve it uncached
            return out
        with _LOCK:
            if key not in _ENTRIES:
                _ENTRIES[key] = blob
                _SIZE[0] += len(blob)
                while _SIZE[0] > _MAX_BYTES and _ENTRIES:
                    _, old = _ENTRIES.popitem(last=False)
                    _SIZE[0] -= len(old)
        return pickle.loads(blob)

    def get_fonts(self, full=False):
        doc = self.parent
        src = getattr(doc, "_rd_src", None)
        if src is None or doc.is_dirty:
            _STATS["font_skip"] += 1
            return orig_get_fonts(self, full=full)
        key = (_digest(src), self.number, "__fonts__", bool(full))
        with _LOCK:
            got = _FONTS.get(key)
            if got is not None:
                _FONTS.move_to_end(key)
        if got is not None:
            _STATS["font_hit"] += 1
            return list(got)              # a fresh list; its entries are tuples of str/int
        _STATS["font_miss"] += 1
        out = orig_get_fonts(self, full=full)
        with _LOCK:
            _FONTS[key] = tuple(tuple(f) for f in out)
            while len(_FONTS) > 4096:
                _FONTS.popitem(last=False)
        return out

    fitz.Document.__init__ = init
    fitz.Page.get_text = get_text
    fitz.Page.get_fonts = get_fonts


def stats() -> dict:
    return dict(_STATS, entries=len(_ENTRIES), bytes=_SIZE[0])
