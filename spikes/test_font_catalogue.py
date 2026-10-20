"""test_font_catalogue.py — font names that are not Google Fonts families are not looked for there.

resolve_full_font's last step guessed fifteen file URLs in the Google Fonts repository for any
family it could not otherwise place. For the names real documents embed — "HelveticaNeueLTStd-
Roman", "CMR10", "CMMI7" — every guess is a 404: the first /webfonts on the IRS 1040 made 135
requests (31 s), on an arXiv paper 300 (76 s), in the server's one request loop. gf_families.json
lists the family folders that exist; a name not among them is not guessed for.

The network is never used here: lookups are counted by a stand-in that fails at once.
"""
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
os.chdir(os.path.join(HERE, "..", "backend"))

import pdf_editor as pe  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + str(detail)[:300] if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


asked = []


def no_network(url, timeout=15):
    asked.append(url)
    raise OSError("no network in this test")


pe.fetch = no_network


def attempts(family, weight=400, style="normal"):
    asked.clear()
    cache = os.path.join(pe._FONT_CACHE_DIR, f"{family.replace(' ', '').replace('-', '')}-{weight}-{style}.ttf")
    if os.path.exists(cache):
        return None                                    # served from disk: says nothing here
    pe._fetch_google_font(family, weight, style)
    return len(asked)


# names real documents embed, which no Google Fonts folder is called
for fam in ("HelveticaNeueLTStd", "CMR10", "CMMI7", "ITCFranklinGothicStd", "MinionPro"):
    n = attempts(fam, 300, "italic")
    check(f"{fam}: not looked for in the catalogue", n == 0, f"{n} requests")

# a real family the cache lacks is still looked for, and only where the catalogue has it
n = attempts("Exo 2", 300, "italic")
check("a real catalogue family (Exo 2) is still looked for", bool(n), n)
check("...only in its own license directory", all("/ofl/exo2/" in u for u in asked), asked[:3])

# without a catalogue, the old behaviour
saved = pe._GF_FAMILIES
pe._GF_FAMILIES = {}
n = attempts("CMR10", 300, "italic")
check("no catalogue file: every directory is tried, as before", n and n >= 10, n)
pe._GF_FAMILIES = saved

# the donor search's folder listing (GitHub API, 60 calls an hour unauthenticated) is not asked
# about a folder the catalogue does not have
import font_extend as fe  # noqa: E402
import urllib.request  # noqa: E402

api_calls = []
_urlopen = urllib.request.urlopen


def no_api(req, *a, **k):
    api_calls.append(getattr(req, "full_url", req))
    raise OSError("no network in this test")


urllib.request.urlopen = no_api
saved_listing = dict(fe._listing_cache)
for key in [k for k in fe._listing_cache if k.endswith("/cmr10") or k.endswith("/helveticaneueltstd")]:
    del fe._listing_cache[key]
for lic in ("ofl", "apache", "ufl"):
    for folder in ("cmr10", "helveticaneueltstd"):
        fe._gh_list_dir(lic, folder)
check("donor search: no API call for folders the catalogue does not have", not api_calls, api_calls[:3])
api_calls.clear()
fe._listing_cache.pop("ofl/exo2", None)
fe._gh_list_dir("ofl", "exo2")
check("donor search: a catalogue folder is still listed", len(api_calls) == 1, api_calls)
urllib.request.urlopen = _urlopen
fe._listing_cache.clear()
fe._listing_cache.update(saved_listing)

# the file itself
cat = json.load(open(pe._GF_FAMILIES_FILE))
check("the catalogue is complete (over 2000 family folders)", sum(len(v) for v in cat.values()) > 2000,
      {k: len(v) for k, v in cat.items()})
manifest = json.load(open(os.path.join(os.path.dirname(pe._GF_FAMILIES_FILE), "donor_manifest.json")))
absent = [k for k in manifest if k.split("/")[1] not in cat.get(k.split("/")[0], [])]
check("every bundled donor family is in it", not absent, absent)

print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
sys.exit(1 if FAIL else 0)
