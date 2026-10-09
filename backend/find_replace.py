"""Find-and-replace on top of the span editor: the logic behind POST /v1/replace.

Pure Python, no PDF code. `plan` turns [{find, replace}] into per-span edits against the
text a document already has; `settle` and `receipt` turn what the engine did into an
account of each match that a workflow can branch on.
"""
import re

MAX_CHANGES = 200
MAX_TEXT = 2000
_KEYS = {"find", "replace", "page", "first_only", "whole_word"}
_APOS, _DQ = "'‘’ʼ", '"“”'
_QUOTES = {**{c: f"[{_APOS}]" for c in _APOS}, **{c: f"[{_DQ}]" for c in _DQ}}


class BadRequest(ValueError):
    pass


def parse_changes(raw) -> list:
    if not isinstance(raw, list) or not raw:
        raise BadRequest("`changes` must be a non-empty list of {find, replace} objects.")
    if len(raw) > MAX_CHANGES:
        raise BadRequest(f"At most {MAX_CHANGES} changes per request.")
    out = []
    for n, c in enumerate(raw, 1):
        if not isinstance(c, dict):
            raise BadRequest(f"Change {n} must be an object with `find` and `replace`.")
        extra = sorted(set(c) - _KEYS)
        if extra:
            raise BadRequest(f"Change {n} has unknown field(s): {', '.join(extra)}. "
                             f"Allowed: {', '.join(sorted(_KEYS))}.")
        find, rep = c.get("find"), c.get("replace")
        if not isinstance(find, str) or not find.strip():
            raise BadRequest(f"Change {n} needs a non-empty `find` text.")
        if not isinstance(rep, str):
            raise BadRequest(f"Change {n} needs a `replace` text (use \"\" to delete).")
        if len(find) > MAX_TEXT or len(rep) > MAX_TEXT:
            raise BadRequest(f"Change {n}: `find` and `replace` are limited to {MAX_TEXT} characters.")
        page = c.get("page")
        if page is not None and (isinstance(page, bool) or not isinstance(page, int) or page < 1):
            raise BadRequest(f"Change {n}: `page` is a page number starting at 1.")
        for k in ("first_only", "whole_word"):
            if k in c and not isinstance(c[k], bool):
                raise BadRequest(f"Change {n}: `{k}` must be true or false.")
        out.append({"find": find, "replace": rep, "page": page,
                    "first_only": c.get("first_only", False),
                    "whole_word": c.get("whole_word", True)})
    return out


def _pattern(find: str, whole: bool, flags=0):
    parts = []
    for tok in re.split(r"(\s+)", find):
        if not tok:
            continue
        if tok.isspace():
            parts.append(r"\s+")          # a space typed here may be a no-break space there
        else:
            parts.append("".join(_QUOTES.get(ch, re.escape(ch)) for ch in tok))
    pat = "".join(parts)
    if whole:
        if find[0].isalnum() or find[0] == "_":
            pat = r"(?<!\w)" + pat
        if find[0].isdigit():
            pat = r"(?<!\d[.,])" + pat      # "240.00" is not inside "1,240.00"
        if find[-1].isalnum() or find[-1] == "_":
            pat += r"(?!\w)"
        if find[-1].isdigit():
            pat += r"(?![.,]\d)"            # nor "3,840" inside "3,840.00"
    return re.compile(pat, flags)


def _diagnose(ch, spans) -> str:
    """Why a text that was asked for is not there, so the caller can act on it."""
    if any(_pattern(ch["find"], ch["whole_word"], re.I).search(sp["text"]) for sp in spans):
        return "case_differs"
    loose = _pattern(ch["find"], False)
    if ch["whole_word"] and any(loose.search(sp["text"]) for sp in spans):
        return "only_inside_a_longer_word_or_number"
    for a, b in zip(spans, spans[1:]):
        if a["page"] == b["page"] and loose.search(a["text"] + " " + b["text"]):
            return "spans_several_lines"
    return "absent"


def plan(spans: list, changes: list) -> dict:
    """Every change is matched against the text the document HAS, never against another
    change's output, so one replacement can't be re-replaced by the next."""
    found = []                            # (span, start, end, change)
    for ci, ch in enumerate(changes):
        pat = _pattern(ch["find"], ch["whole_word"])
        done = False
        for si, sp in enumerate(spans):
            if ch["page"] and sp["page"] + 1 != ch["page"]:
                continue
            for m in pat.finditer(sp["text"]):
                found.append((si, m.start(), m.end(), ci))
                if ch["first_only"]:
                    done = True
                    break
            if done:
                break
    found.sort()

    matches, edits = [], {}
    by_span = {}
    for si, a, b, ci in found:
        by_span.setdefault(si, []).append((a, b, ci))
    for si, items in by_span.items():
        text = spans[si]["text"]
        out, pos, last = [], 0, -1
        for a, b, ci in items:
            m = {"change": ci, "span": si, "page": spans[si]["page"] + 1, "status": "pending"}
            if a < last:
                m.update(status="unchanged", reason="overlaps_another_change",
                         message="Another change in this request matched the same words.")
            else:
                out += [text[pos:a], changes[ci]["replace"]]
                pos = last = b
            matches.append(m)
        new = "".join(out) + text[pos:]
        if new == text:
            for m in matches:
                if m["span"] == si and m["status"] == "pending":
                    m["status"] = "already"
        else:
            edits[si] = new
    matches.sort(key=lambda m: (m["change"], m["span"]))
    missing = {ci: _diagnose(ch, spans) for ci, ch in enumerate(changes)
               if not any(m["change"] == ci for m in matches)}
    return {"edits": edits, "matches": matches, "missing": missing}


def settle(p: dict, spans: list, report: dict, left_unchanged: set) -> None:
    """Say, for each match, what the engine did with its span."""
    refusals = (report.get("in_place") or {}).get("refusals") or []
    for m in p["matches"]:
        if m["status"] != "pending":
            continue
        key = spans[m["span"]]["text"][:60]
        mine = [r for r in refusals if r.get("text") == key]
        dead = [r for r in mine if r.get("reason") in left_unchanged or r.get("reason") == "invisible_text"]
        if dead:
            m.update(status="unchanged", reason=dead[0]["reason"], message=dead[0].get("message"))
        else:
            m["status"] = "redrawn" if mine else "in_place"


def receipt(changes: list, p: dict, report: dict, readback, meta: dict) -> dict:
    """`readback(page, text)` → how many times `text` reads on that page of the output."""
    per, good = [], 0
    for ci, ch in enumerate(changes):
        ms = [m for m in p["matches"] if m["change"] == ci]
        done = [m for m in ms if m["status"] in ("in_place", "redrawn")]
        already = sum(m["status"] == "already" for m in ms)
        row = {"find": ch["find"], "replace": ch["replace"], "found": len(ms),
               "edited": len(done),
               "in_place": sum(m["status"] == "in_place" for m in ms),
               "redrawn": sum(m["status"] == "redrawn" for m in ms)}
        if done and readback:
            pages = {}
            for m in done:
                pages[m["page"]] = pages.get(m["page"], 0) + 1
            if ch["replace"].strip():     # the new text reads on the page
                row["verified"] = all(readback(pg, ch["replace"]) >= n for pg, n in pages.items())
            else:                         # a deletion: the old text is gone from it
                row["verified"] = all(readback(pg, ch["find"]) <= readback(pg, ch["find"], True) - n
                                      for pg, n in pages.items())
        problems = [{"page": m["page"], "reason": m["reason"], "message": m.get("message")}
                    for m in ms if m["status"] == "unchanged"]
        if problems:
            row["problems"] = problems
        if not ms:
            row["why_not_found"] = p["missing"].get(ci, "absent")
        good += bool(ms) and row["edited"] + already == row["found"] and row.get("verified", True)
        per.append(row)
    edited = sum(r["edited"] for r in per)
    status = "ok" if good == len(per) else ("partial" if edited else "unchanged")
    reflow = (report.get("in_place") or {}).get("not_rewrapped") or []
    return {"ok": status == "ok", "status": status, "edited": edited, "changes": per,
            "warnings": list(report.get("warnings") or []),
            "not_reflowed": [{"page": r.get("page", 0) + 1, "text": r.get("text"),
                              "message": r.get("message")} for r in reflow],
            **meta}
