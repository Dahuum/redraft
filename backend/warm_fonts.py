"""Build-time font warm-up (best-effort).

Puts every font the engine can ask for into .font_cache, so a request never waits for the
network — and a container run with REDRAFT_OFFLINE=1 (or `docker --network none`) has all of
them. Anything not bundled here is still fetched on demand when the network is allowed.
Failures are non-fatal (the Dockerfile runs this with `|| true`).

  python warm_fonts.py             warm everything
  python warm_fonts.py --manifest  rewrite donor_manifest.json from the catalogue (needs the
                                   network; set GITHUB_TOKEN, the unauthenticated API allows 60/hr)

WARM_SET=full also fetches the CJK and emoji fallbacks (about 60 MB); the default leaves them out.
"""
import json
import os
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor

warnings.simplefilter("ignore")

try:
    import font_extend as fe
    import pdf_editor as pe
    import type1_extend as t1
except Exception as exc:  # noqa: BLE001
    print(f"warm: could not import engine ({exc}); skipping")
    raise SystemExit(1 if os.environ.get("WARM_STRICT") == "1" else 0)

NAMES = [
    "Arial", "Arial-Bold", "Arial-Italic", "Arial-BoldItalic",
    "ArialMT", "Arial-BoldMT",
    "Calibri", "Calibri-Bold",
    "Inter", "Inter-Bold",
]

# Families a document is likely to be set in, besides the substitutes the engine's own tables
# name. The donor path looks a font up under its own name first, so these are the ones whose
# missing letters can be borrowed from the genuine typeface.
COMMON = [
    "Arimo", "Tinos", "Cousine", "Carlito", "Caladea", "Inter", "Roboto", "Roboto Slab",
    "Roboto Mono", "Open Sans", "Lato", "Montserrat", "Poppins", "Raleway", "Nunito",
    "Nunito Sans", "Source Sans 3", "Source Serif 4", "Source Code Pro", "Merriweather",
    "Playfair Display", "Oswald", "PT Sans", "Rubik", "Barlow", "Karla", "Mulish", "Manrope",
    "Jost", "Outfit", "Urbanist", "Questrial", "Fraunces", "Didact Gothic", "Work Sans",
    "DM Sans", "DM Serif Display", "Fira Sans", "IBM Plex Sans", "Lora", "Noto Serif",
    "Libre Baskerville", "EB Garamond", "Sacramento",
]

SCRIPT_FALLBACKS_BIG = ("Noto Sans SC", "Noto Sans JP", "Noto Sans KR", "Noto Sans TC", "Noto Emoji")

# The AMS Computer Modern originals LaTeX documents embed (type1_extend borrows from them).
CM = ([f"cmr{n}" for n in (5, 6, 7, 8, 9, 10, 12, 17)] + [f"cmbx{n}" for n in (5, 6, 7, 8, 9, 10, 12)]
      + [f"cmti{n}" for n in (7, 8, 9, 10, 12)] + [f"cmsl{n}" for n in (8, 9, 10, 12)]
      + [f"cmtt{n}" for n in (8, 9, 10, 12)] + [f"cmss{n}" for n in (8, 9, 10, 12, 17)]
      + [f"cmmi{n}" for n in (5, 6, 7, 8, 9, 10, 12)] + [f"cmsy{n}" for n in (5, 6, 7, 8, 9, 10)]
      + [f"msam{n}" for n in (5, 7, 10)] + [f"msbm{n}" for n in (5, 7, 10)]
      + ["cmbxti10", "cmbxsl10", "cmssbx10", "cmssi10", "cmmib10", "cmbsy10", "cmex10",
         "cmcsc10", "cmu10", "cmitt10", "cmb10", "eufm10", "eurm10", "eusm10"])


def donor_families():
    fams = {v[0] for v in pe._FONT_SUBSTITUTES.values()}
    fams |= set(fe._DONOR_ONLY_SUBSTITUTES.values()) | set(fe._DONOR_SUBSTITUTE_KEYS.values())
    fams |= set(fe._KNOWN_RENAMES.values()) | set(COMMON)
    return sorted(fams)


def write_manifest():
    out = {}
    for fam in donor_families():
        folder = fe._family_key(fam)
        for lic in fe._LICENSE_DIRS:
            files = fe._gh_list_dir(lic, folder)
            if files:
                out[f"{lic}/{folder}"] = sorted(f for f in files if f.lower().endswith(".ttf"))
                break
        else:
            print(f"manifest: {fam} not found")
    with open(fe._MANIFEST, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=0, sort_keys=True)
        f.write("\n")
    print(f"manifest: {len(out)} families")


WORKERS = 8   # the CTAN mirror redirect is erratic (0.5 s to 20 s a file); one at a time takes minutes


def _each(jobs, fn):
    """Run fn(job) over a pool; the number that came back truthy. A failure costs that job only."""
    def one(job):
        try:
            return bool(fn(job))
        except Exception as exc:  # noqa: BLE001
            print(f"warm: {job} -> {exc}")
            return False
    with ThreadPoolExecutor(WORKERS) as pool:
        return sum(pool.map(one, jobs))


def warm_donors():
    with open(fe._MANIFEST, encoding="utf-8") as f:
        manifest = json.load(f)
    urls = []
    for key, files in sorted(manifest.items()):
        lic, folder = key.split("/")
        wanted = {c[1] for c in (fe._pick_donor_file(files, w, s) for w in (400, 700)
                                 for s in ("normal", "italic")) if c}
        urls += [f"{pe._GF_RAW}/{lic}/{folder}/{name}" for name in sorted(wanted)]
    got = _each(urls, pe.fetch)
    print(f"warm: {got}/{len(urls)} donor files", flush=True)
    return len(urls) - got


def main():
    if "--manifest" in sys.argv:
        write_manifest()
        return
    cached = 0
    for name in NAMES:
        try:
            if pe.resolve_full_font(name):
                cached += 1
        except Exception as exc:  # noqa: BLE001
            print(f"warm: {name} -> {exc}")
    print(f"warm: resolved {cached}/{len(NAMES)} font names")

    short = (len(NAMES) - cached) + warm_donors()

    # The script fallbacks (pdf_editor._FALLBACK_FAMILIES): a replacement with letters the document's
    # font lacks is drawn in whichever of these covers them. The production container has no system
    # fonts for these, so without them in the cache that edit is refused.
    full = os.environ.get("WARM_SET", "").lower() == "full"
    got = 0
    for fam in pe._FALLBACK_FAMILIES:
        if fam in SCRIPT_FALLBACKS_BIG and not full:
            continue
        try:
            got += bool(pe._get_fallback_font(fam))
        except Exception as exc:  # noqa: BLE001
            print(f"warm: {fam} -> {exc}")
    wanted = sum(1 for fam in pe._FALLBACK_FAMILIES if full or fam not in SCRIPT_FALLBACKS_BIG)
    print(f"warm: {got} script fallbacks")
    short += wanted - got

    cm = _each(CM, lambda n: t1.donor_program(n) is not None)
    print(f"warm: {cm}/{len(CM)} Computer Modern originals", flush=True)
    short += len(CM) - cm

    # An image meant to run with no network must not be built half-stocked: with WARM_STRICT=1 a
    # font that could not be fetched fails the build instead of surfacing as a refused edit later.
    if short and os.environ.get("WARM_STRICT") == "1":
        print(f"warm: {short} fonts could not be fetched; failing the build (WARM_STRICT=1)")
        sys.exit(1)


main()
