"""test_font_isolation.py — one document's fonts must never reach another's.

The server used to copy every uploaded PDF's embedded fonts into the shared
.font_cache under the family name, and /font installed a user's upload there
too. The first file to supply a family became that family for every later
user: a full commercial Calibri lifted from one customer's document was being
served to all of them, and a crafted PDF could plant a font whose glyphs draw
other letters.

This builds exactly that attack — a complete font under a unique family name
whose 'a' draws an 'o' — and checks it stays inside its own request.
"""
import contextvars
import io
import os
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
warnings.simplefilter("ignore")

import fitz  # noqa: E402
from fontTools.ttLib import TTFont  # noqa: E402

import api  # noqa: E402
import pdf_editor as pe  # noqa: E402

FAIL = []
FAMILY = "ZzPoisonSans"


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def poisoned_font() -> bytes:
    """A complete font named ZzPoisonSans whose 'a' is really an 'o'."""
    src = pe.resolve_full_font("DejaVuSans")
    tt = TTFont(io.BytesIO(src))
    cmap = tt.getBestCmap()
    for table in tt["cmap"].tables:
        if ord("a") in table.cmap:
            table.cmap[ord("a")] = cmap[ord("o")]
    for rec in tt["name"].names:
        if rec.nameID in (1, 3, 4, 6, 16):
            rec.string = FAMILY
    buf = io.BytesIO()
    tt.save(buf)
    return buf.getvalue()


POISON = poisoned_font()
d = fitz.open()
p = d.new_page()
p.insert_font(fontname="F0", fontbuffer=POISON)
p.insert_text((72, 100), "a banana and a data table", fontname="F0", fontsize=12)
attack = d.tobytes()
d.close()
shared = pe._FONT_CACHE_DIR
key = pe.font_cache_key(FAMILY)
before = set(os.listdir(shared))


def in_request(fn, *a):
    """Run *fn* the way a request runs: in a context of its own."""
    return contextvars.copy_context().run(fn, *a)


def attacker_request():
    api._ingest_embedded_fonts(attack, None)
    return pe._DOC_FONTS.get()


overlay = in_request(attacker_request)
check("the attacking document's fonts are not written to the shared cache",
      not os.path.exists(os.path.join(shared, key)) and set(os.listdir(shared)) == before,
      str(set(os.listdir(shared)) - before))
check("...but that document can still use its own complete font for itself",
      bool(overlay) and any(v == POISON or len(v) > 1000 for v in overlay.values()))


def victim_request():
    api._ingest_embedded_fonts(b"%PDF-1.4 not a real document", None)
    pe._RESOLVED.pop(FAMILY, None)
    return pe.resolve_full_font(FAMILY)


got = in_request(victim_request)
check("another request asking for that family never receives the attacker's font",
      got != POISON, "the poisoned program was served")

# /font: a user's upload is theirs alone.
ud = api._user_font_dir("user-A")
check("a user's font directory is private to them and outside the shared names",
      ud.startswith(os.path.join(shared, "users")) and ud != api._user_font_dir("user-B"))
os.makedirs(ud, exist_ok=True)
path = os.path.join(ud, key)
open(path, "wb").write(POISON)
try:
    check("user A's requests see user A's font", key in api._user_fonts("user-A"))
    check("user B's requests do not", key not in api._user_fonts("user-B"))
    check("a guest's requests do not", api._user_fonts(None) == {})
finally:
    os.remove(path)

print("\n" + "=" * 70)
print("RESULT:", "ALL PASS" if not FAIL else f"{len(FAIL)} FAILED -> {FAIL}")
sys.exit(1 if FAIL else 0)
