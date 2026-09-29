"""Single-field edits across the corpus — the main sweep.

Five shapes per field, judged on the RENDERED OUTPUT as a delta against the
same page unedited. PER_DOC controls how many fields per document.

    PER_DOC=4 python3 audit_edit.py
    RD_BACKEND=/tmp/wt/backend python3 audit_edit.py   # A/B a fix
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: E402
from _common import (HARD, corpus_docs, line_reads, moved_text, output_defects,  # noqa: E402
                     page_facts, stranded, edit_tells)
from api import extract_spans, apply_replacements  # noqa: E402

import warnings  # noqa: E402
warnings.simplefilter("ignore")

PER_DOC = int(os.environ.get("PER_DOC", "4"))


def shapes(old):
    return (
        ("same-len", "".join("Wxqzkj0123456789"[(i * 5) % 16] for i in range(len(old)))),
        ("shorter", old[:max(3, len(old) // 2)]),
        ("longer", old + " Wxqzkj"),
        ("much-longer", old + " " + "Wxqzkj " * 5),
        ("accents", "Zoé Ångström-Ñuñez " + old[:10]),
        # What people actually do: change ONE word and keep the rest of the
        # line. Every other shape rewrites the whole span, so the commonest
        # edit of all went unmeasured.
        ("one-word", _one_word(old)),
    )


def _one_word(old):
    words = old.split(" ")
    if len(words) < 2:
        return old[:max(1, len(old) // 2)] + "Qz"
    i = max(range(len(words)), key=lambda k: len(words[k]))
    w = words[i]
    words[i] = ("Wxqzkjmb" * 4)[:max(2, len(w))]
    return " ".join(words)


tot = {"tells": 0, "n": 0, "inplace": 0, "redrawn": 0, "refused": 0, "defects": 0, "crash": 0}
by_shape, reasons, rows = {}, {}, []

for label, path in corpus_docs():
    data = open(path, "rb").read()
    try:
        # What every API endpoint does first: adopt the PDF's own fonts (and, for a Type3 font,
        # its genuine family) for this request. Without it the audit judged a different engine.
        import api as _api
        _api._ingest_embedded_fonts(data)
    except Exception:  # noqa: BLE001
        pass
    try:
        spans = extract_spans(data)
    except Exception as exc:  # noqa: BLE001
        print("=== %-20s EXTRACT CRASH %s" % (label, type(exc).__name__))
        continue
    usable = [s for s in spans if len(s["text"].strip()) >= 6 and s.get("size", 0) > 5]
    random.Random(11).shuffle(usable)
    d0 = {"n": 0, "refused": 0, "defects": 0, "crash": 0}
    notes = []
    for sd in usable[:PER_DOC]:
        old = sd["text"].strip()
        page = sd.get("page", 0)
        base = page_facts(data, page)
        for shape, new in shapes(old):
            tot["n"] += 1
            d0["n"] += 1
            sh = by_shape.setdefault(shape, {"n": 0, "clean": 0, "refused": 0, "defect": 0, "why": {}})
            sh["n"] += 1
            try:
                out, rep = apply_replacements(data, [(sd, new)], preserve_size=True,
                                              try_inplace=True)
            except Exception as exc:  # noqa: BLE001
                tot["crash"] += 1
                d0["crash"] += 1
                notes.append("CRASH %s on %r" % (type(exc).__name__, old[:18]))
                continue
            hard = [r for r in (rep["in_place"].get("refusals") or [])
                    if r.get("reason") in HARD]
            if hard:
                tot["refused"] += 1
                d0["refused"] += 1
                sh["refused"] += 1
                for r in hard:
                    if r["reason"] == "crowds_neighbour":
                        notes.append("%-12s %r -> refused (crowds_neighbour) — check it is a real crowd" % (shape, old[:20]))
                    reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
                    sh["why"][r["reason"]] = sh["why"].get(r["reason"], 0) + 1
                continue
            if rep["in_place"]["count"]:
                tot["inplace"] += 1
            else:
                tot["redrawn"] += 1
            rf = next((r for r in (rep["in_place"].get("reflowed") or [])), None)
            bad = output_defects(out, page, new, base) + moved_text(data, out, page, sd, reflowed=rf)
            bad += stranded(data, out, page, field=sd["bbox"], edit=(sd["text"], new))
            if not rf:
                bad += line_reads(data, out, page, sd, new)
            if rf:
                tot["reflowed"] = tot.get("reflowed", 0) + 1
            try:
                tells = edit_tells(data, out, page, sd, new, redrawn=not rep['in_place']['count']) if not bad else []
            except Exception:  # noqa: BLE001 — a detector bug must not hide the audit
                tells = []
            if tells:
                tot["tells"] += 1
                notes.append("TELL %-12s %r -> %s" % (shape, old[:20], tells))
            if bad:
                tot["defects"] += 1
                d0["defects"] += 1
                sh["defect"] += 1
                notes.append("%-12s %r -> %s" % (shape, old[:20], bad))
            else:
                sh["clean"] += 1
    ok = d0["n"] - d0["refused"] - d0["defects"] - d0["crash"]
    rows.append((label, d0["n"], ok, d0["refused"], d0["defects"], d0["crash"]))
    flag = "  <<<" if (d0["defects"] or d0["crash"]) else ""
    print("=== %-20s n=%3d clean=%3d refused=%3d DEFECT=%3d crash=%3d%s"
          % (label, d0["n"], ok, d0["refused"], d0["defects"], d0["crash"], flag))
    for nl in notes[:4]:
        print("       ", nl)

n = tot["n"] or 1
clean = n - tot["refused"] - tot["defects"] - tot["crash"]
print("\n" + "=" * 74)
print("%-22s%5s%8s%10s%9s%8s" % ("document", "n", "clean", "refused", "defect", "crash"))
for r in rows:
    print("%-22s%5d%8d%10d%9d%8d" % r)
print("-" * 74)
print("VISIBLE TELLS (size / typeface / ruling, not counted as defects): %d" % tot["tells"])
print("TOTAL %d   clean %d (%.1f%%)   refused %d (%.1f%%)   DEFECTS %d   crashes %d"
      % (tot["n"], clean, 100.0 * clean / n, tot["refused"], 100.0 * tot["refused"] / n,
         tot["defects"], tot["crash"]))
print("  in place %d   redrawn %d   (of the in-place, re-wrapped paragraphs: %d)"
      % (tot["inplace"], tot["redrawn"], tot.get("reflowed", 0)))
print("  refusal reasons: %s" % reasons)
print("\n%-14s%5s%8s%10s%9s   why refused" % ("edit shape", "n", "clean", "refused", "defect"))
for k, v in by_shape.items():
    print("%-14s%5d%8d%10d%9d   %s" % (k, v["n"], v["clean"], v["refused"], v["defect"], v["why"]))
sys.exit(1 if (tot["defects"] or tot["crash"]) else 0)
