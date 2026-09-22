"""test_annex_numbers.py — an annex must not invent money.

parse_num and fmt_num were hard-wired to the fr-MA convention (. groups,
, is the decimal). An annex written in English arithmetic was therefore read
wrong and rewritten wrong, silently:

    parse_num("950.00")     -> 95000.0      (the dot read as grouping)
    12 x that               -> 1140000.0
    fmt_num(...)            -> "1.140.000,00"   where "11,400.00" belonged

and the Total HT was recomputed to match, so the page looked like a finished
invoice annex carrying numbers 100x too large. Nothing in the pipeline could
notice: the arithmetic was self-consistent.

Both conventions are covered here, in both directions, because fixing one by
breaking the other would be no better.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
from annex_model import parse_num, fmt_num, number_style, doc_number_style  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + detail if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def close(a, b):
    return a is not None and abs(a - b) < 1e-9


# The fr-MA readings this engine was built on must not move.
for text, want in (("1.884,30", 1884.3), ("6.281", 6281.0), ("0,3", 0.3),
                   ("2.500,00", 2500.0), ("26.800,00", 26800.0)):
    check(f"fr-MA {text!r} still reads {want}", close(parse_num(text), want),
          f"got {parse_num(text)}")

# The English readings that were silently wrong.
for text, want in (("950.00", 950.0), ("11,400.00", 11400.0), ("1,200.00", 1200.0),
                   ("26,800.00", 26800.0), ("0.3", 0.3)):
    check(f"en {text!r} reads {want}", close(parse_num(text), want),
          f"got {parse_num(text)}")

check("the 12 x 950.00 line that started this is 11400",
      close(12 * (parse_num("950.00") or 0), 11400.0),
      f"got {12 * (parse_num('950.00') or 0)}")

# Style detection per number.
for text, want in (("1.884,30", "eu"), ("11,400.00", "en"), ("6.281", "eu"),
                   ("950.00", "en"), ("0,3", "eu")):
    check(f"style of {text!r} is {want}", number_style(text) == want, number_style(text))

# Formatting round-trips in the convention it was given.
check("fmt eu", fmt_num(11400.0) == "11.400,00", fmt_num(11400.0))
check("fmt en", fmt_num(11400.0, 2, "en") == "11,400.00", fmt_num(11400.0, 2, "en"))
check("a parsed number formats back to itself (eu)",
      fmt_num(parse_num("1.884,30")) == "1.884,30")
check("a parsed number formats back to itself (en)",
      fmt_num(parse_num("11,400.00"), 2, "en") == "11,400.00")

# A document's convention is decided by majority over its own cells, so one
# odd value cannot flip it.
eu_model = {"items": [{"amount": "11.400,00", "unitPrice": "950,00", "qty": "12"},
                      {"amount": "1.200,00", "unitPrice": "600,00", "qty": "2"}],
            "total": {"value": "12.600,00"}}
en_model = {"items": [{"amount": "11,400.00", "unitPrice": "950.00", "qty": "12"},
                      {"amount": "1,200.00", "unitPrice": "600.00", "qty": "2"}],
            "total": {"value": "12,600.00"}}
check("a French annex is detected as fr", doc_number_style(eu_model) == "eu")
check("an English annex is detected as en", doc_number_style(en_model) == "en")
mixed = {"items": [{"amount": "11,400.00", "unitPrice": "950.00", "qty": "12"},
                   {"amount": "1,200.00", "unitPrice": "600.00", "qty": "2"},
                   {"amount": "1.500,00", "unitPrice": "500,00", "qty": "3"}],
         "total": {"value": "14,100.00"}}
check("one odd cell does not flip the document's convention",
      doc_number_style(mixed) == "en", doc_number_style(mixed))

print(f"\n{'=' * 70}")
if FAIL:
    print(f"RESULT: {len(FAIL)} FAILED -> {FAIL}")
    sys.exit(1)
print("RESULT: ALL PASS")
