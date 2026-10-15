"""test_font_names.py — a font name the extractor cut short still resolves to the right style.

PyMuPDF cuts a span's font name at 24 characters, and the style is what's at the end:
"ITCFranklinGothicStd-Dem" is "-Demi" (SemiBold), "LiberationSans-BoldItali" is "-BoldItalic".
Read as given, the weight or the slant was lost, and the Bold Italic run on a Chrome-printed page
resolved to its bare embedded subset instead of the installed LiberationSans-BoldItalic.
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
os.chdir(os.path.join(HERE, "..", "backend"))

import fitz  # noqa: E402
import api  # noqa: E402
import pdf_editor as pe  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + str(detail)[:300] if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def doc_with_fonts(names):
    """A one-page PDF whose fonts carry exactly these base names (no font program needed)."""
    objs = ["<</Type/Catalog/Pages 2 0 R>>", "<</Type/Pages/Kids[3 0 R]/Count 1>>"]
    res = " ".join(f"/F{i} {4 + i} 0 R" for i in range(len(names)))
    content_obj = 4 + len(names)
    objs.append(f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 300]/Resources<</Font<<{res}>>>>/Contents {content_obj} 0 R>>")
    for n in names:
        objs.append(f"<</Type/Font/Subtype/TrueType/BaseFont/{n}/FirstChar 32/LastChar 126/Encoding/WinAnsiEncoding>>")
    body = "".join(f"BT /F{i} 10 Tf 20 {250 - 20 * i} Td (Hi) Tj ET\n" for i in range(len(names)))
    objs.append(f"<</Length {len(body)}>>stream\n{body}endstream")
    raw = "%PDF-1.4\n" + "".join(f"{k + 1} 0 obj{o}endobj\n" for k, o in enumerate(objs)) + "trailer<</Root 1 0 R>>"
    return fitz.open(stream=raw.encode(), filetype="pdf").tobytes()


api._ingest_embedded_fonts(doc_with_fonts(["LiberationSans-BoldItalic", "ITCFranklinGothicStd-Demi",
                                           "HerrVonMuellerhoff-Regular"]), "")
check("cut Bold Italic is bold italic", pe._parse_font_name("LiberationSans-BoldItali") == ("LiberationSans", 700, "italic"),
      pe._parse_font_name("LiberationSans-BoldItali"))
check("cut Demi is SemiBold", pe._parse_font_name("ITCFranklinGothicStd-Dem")[1:] == (600, "normal"),
      pe._parse_font_name("ITCFranklinGothicStd-Dem"))
check("cut Regular is the bare family", pe._parse_font_name("HerrVonMuellerhoff-Regul")[0] == "HerrVonMuellerhoff",
      pe._parse_font_name("HerrVonMuellerhoff-Regul"))
check("an uncut name is unchanged", pe._parse_font_name("LiberationSans-Bold") == ("LiberationSans", 700, "normal"))

# ambiguous: two full names start with the same 24 characters -> left as it is
api._ingest_embedded_fonts(doc_with_fonts(["AbcdefghijklmnopqrstuvwxBold", "AbcdefghijklmnopqrstuvwxLight"]), "")
check("an ambiguous cut is not guessed", pe._parse_font_name("Abcdefghijklmnopqrstuvwx")[0] == "Abcdefghijklmnopqrstuvwx",
      pe._parse_font_name("Abcdefghijklmnopqrstuvwx"))

# a short name that is a prefix of a longer one is never "expanded"
api._ingest_embedded_fonts(doc_with_fonts(["Arial", "Arial-BoldItalicMT"]), "")
check("a short name is never expanded", pe._parse_font_name("Arial") == ("Arial", 400, "normal"), pe._parse_font_name("Arial"))

# no document in this context -> exactly the old behaviour
pe._DOC_FULLNAMES.set(None)
check("without a document nothing changes", pe._parse_font_name("LiberationSans-BoldItali")[2] == "normal")

print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
sys.exit(1 if FAIL else 0)
