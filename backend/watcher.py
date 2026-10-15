"""watcher.py — a folder that edits PDFs.

    <dir>/replace.json   what to change: [{"find": "Acme Ltd", "replace": "Acme Group"}, ...]
    <dir>/in/            drop PDFs here
    <dir>/out/           the edited PDF, and its receipt beside it (name.pdf, name.receipt.json)
    <dir>/done/          the original, once it has been handled
    <dir>/failed/        a PDF that could not be edited as asked, with name.error.json saying why

It is a client of the server's own POST /v1/replace, so a file dropped in a folder is edited by
the same engine, and answered with the same receipt, as a call made over HTTP.

A file reaches out/ only if at least one change was made and none of the matches it found was
left half-done. Rules that matched nothing in this document are noted in the receipt and do not
hold it back; put {"changes": [...], "strict": true} in replace.json to require every rule.
A file that cannot be edited goes to failed/ — it is never dropped and never passed through
as if it had been corrected.

Inside the container this starts with the server when REDRAFT_WATCH names a folder. Standalone,
against any Redraft server:

    python watcher.py --dir ./data --url http://redraft.internal:7860 --key $REDRAFT_API_KEY
"""
import argparse
import base64
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

POLL = float(os.environ.get("REDRAFT_WATCH_INTERVAL", "2"))
ATTEMPTS = 3          # a server error is retried this many times, then the file goes to failed/
SKIP = (".", "~$")


def log(msg: str) -> None:
    print(f"watch: {msg}", flush=True)


def load_rules(root: Path):
    """(changes, strict), or raises ValueError saying what is wrong with replace.json."""
    path = root / "replace.json"
    if not path.is_file():
        raise ValueError(f"{path} is missing. Put the changes in it, e.g. "
                         '[{"find": "Acme Ltd", "replace": "Acme Group"}]')
    try:
        raw = json.loads(path.read_text("utf-8-sig"))
    except ValueError as exc:
        raise ValueError(f"{path} is not valid JSON ({exc}).")
    strict = False
    if isinstance(raw, dict):
        strict, raw = raw.get("strict") is True, raw.get("changes")
    if (not isinstance(raw, list) or not raw
            or not all(isinstance(c, dict) and isinstance(c.get("find"), str) and c["find"]
                       and isinstance(c.get("replace"), str) for c in raw)):
        raise ValueError(f'{path} must hold a list of {{"find": "...", "replace": "..."}}.')
    return [{"find": c["find"], "replace": c["replace"]} for c in raw], strict


def judge(receipt: dict, strict: bool):
    """(deliver, reason). The receipt is what the engine says it did, and it is the only thing believed."""
    if not receipt.get("edited"):
        return False, "nothing was changed: " + _why(receipt)
    if strict and receipt.get("status") != "ok":
        return False, "not every change could be made: " + _why(receipt)
    bad = [c for c in receipt.get("changes", []) if c.get("problems") or c.get("verified") is False]
    if bad:
        return False, "a match was found but not changed cleanly: " + _why(receipt)
    return True, "ok"


def _why(receipt: dict) -> str:
    out = []
    for c in receipt.get("changes", []):
        if c.get("problems"):
            out.append(f'"{c["find"]}": ' + "; ".join(p.get("message") or p.get("reason") or "?"
                                                    for p in c["problems"]))
        elif c.get("verified") is False:
            out.append(f'"{c["find"]}": the new text did not read back')
        elif not c.get("found"):
            out.append(f'"{c["find"]}": not found ({c.get("why_not_found", "absent")})')
    return " | ".join(out) or "no detail"


def free_name(folder: Path, name: str) -> Path:
    target = folder / name
    n = 2
    while target.exists():
        target = folder / f"{Path(name).stem}-{n}{Path(name).suffix}"
        n += 1
    return target


def write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name("." + path.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class Retry(Exception):
    """The server could not be asked; the file stays where it is, and the wait does not count against it."""


def ask(url: str, key: str, pdf: bytes, name: str, changes: list) -> tuple:
    body = json.dumps({"pdf": base64.b64encode(pdf).decode(), "changes": changes, "filename": name}).encode()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-API-Key"] = key
    req = urllib.request.Request(url.rstrip("/") + "/v1/replace", body, headers)
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read())
        except ValueError:
            return exc.code, {"detail": f"HTTP {exc.code}"}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise Retry(f"the server did not answer ({exc})")


class Folder:
    def __init__(self, root, url: str, key: str = ""):
        self.root, self.url, self.key = Path(root), url, key
        for sub in ("in", "out", "done", "failed"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        self.seen: dict = {}       # path -> (size, mtime_ns) at the previous poll
        self.tries: dict = {}
        self.said = ""

    def say_once(self, msg: str) -> None:
        """A standing problem (no rules, wrong key, server down) is reported once, not every poll."""
        if msg != self.said:
            log(msg)
            self.said = msg

    def candidates(self):
        found = []
        for p in (self.root / "in").iterdir():
            if p.is_file() and p.suffix.lower() == ".pdf" and not p.name.startswith(SKIP):
                found.append(p)
        return sorted(found, key=lambda p: p.stat().st_mtime)

    def settled(self, p: Path) -> bool:
        """Copied in full: same size and time as last poll, and not touched in the last second."""
        st = p.stat()
        sig = (st.st_size, st.st_mtime_ns)
        was, self.seen[p] = self.seen.get(p), sig
        return was == sig and st.st_size > 0 and time.time() - st.st_mtime > 1.0

    def fail(self, p: Path, reason: str, receipt=None) -> None:
        target = free_name(self.root / "failed", p.name)
        shutil.move(str(p), target)
        info = {"file": p.name, "reason": reason}
        if receipt:
            info["receipt"] = receipt
        write_atomic(target.with_suffix(".error.json"), json.dumps(info, indent=2, ensure_ascii=False).encode())
        log(f"{p.name} -> failed/ ({reason})")

    def handle(self, p: Path) -> None:
        try:
            changes, strict = load_rules(self.root)
        except ValueError as exc:
            self.say_once(str(exc))
            return
        pdf = p.read_bytes()
        try:
            status, answer = ask(self.url, self.key, pdf, p.name, changes)
        except Retry as exc:
            self.say_once(f"{exc}; waiting")
            return
        if status in (401, 403):
            self.say_once("the server refused the key: set REDRAFT_API_KEY / --key to one it accepts")
            return
        self.said = ""
        if status >= 500:
            self.retry(p, f"the server failed ({status}: {answer.get('detail')})")
            return
        if status != 200:
            self.fail(p, str(answer.get("detail") or f"HTTP {status}"), answer.get("receipt"))
            return
        receipt = answer.get("receipt") or {}
        deliver, reason = judge(receipt, strict)
        if not (deliver and answer.get("pdf")):
            self.fail(p, reason, receipt)
            return
        out = free_name(self.root / "out", p.name)
        write_atomic(out, base64.b64decode(answer["pdf"]))
        write_atomic(out.with_suffix(".receipt.json"), json.dumps(receipt, indent=2, ensure_ascii=False).encode())
        shutil.move(str(p), free_name(self.root / "done", p.name))
        log(f"{p.name} -> out/{out.name} ({receipt.get('edited')} edited, {receipt.get('ms')} ms)")

    def retry(self, p: Path, why: str) -> None:
        self.tries[p] = self.tries.get(p, 0) + 1
        if self.tries[p] >= ATTEMPTS:
            self.fail(p, why)
        else:
            log(f"{p.name}: {why}; will try again ({self.tries[p]}/{ATTEMPTS})")

    def poll(self) -> int:
        """One pass over in/. Returns how many files were looked at."""
        n = 0
        live = set()
        for p in self.candidates():
            live.add(p)
            if not self.settled(p):
                continue
            n += 1
            try:
                self.handle(p)
            except Exception as exc:  # noqa: BLE001 — one bad file must not stop the folder
                self.retry(p, f"{type(exc).__name__}: {exc}")
        for gone in set(self.seen) - live:
            self.seen.pop(gone, None)
            self.tries.pop(gone, None)
        return n


def wait_for_server(url: str, seconds: float = 120, stop=None) -> bool:
    end = time.time() + seconds
    while time.time() < end and not (stop and stop.is_set()):
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=3):
                return True
        except Exception:  # noqa: BLE001
            time.sleep(1)
    return False


def run(root, url: str, key: str = "", stop=None) -> None:
    if not wait_for_server(url, stop=stop):
        log(f"the server at {url} never answered; not watching {root}")
        return
    folder = Folder(root, url, key)
    log(f"watching {folder.root}/in -> out  (rules: replace.json)")
    while not (stop and stop.is_set()):
        folder.poll()
        time.sleep(POLL)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Edit every PDF dropped in a folder with POST /v1/replace.")
    ap.add_argument("--dir", default=os.environ.get("REDRAFT_WATCH", "."), help="the folder holding replace.json and in/")
    ap.add_argument("--url", default=os.environ.get("REDRAFT_URL", "http://127.0.0.1:7860"))
    ap.add_argument("--key", default=os.environ.get("REDRAFT_API_KEY", "").split(",")[0].strip())
    ap.add_argument("--once", action="store_true", help="handle what is in in/ now and exit")
    a = ap.parse_args(argv)
    if not a.once:
        run(a.dir, a.url, a.key)
        return 0
    if not wait_for_server(a.url, 10):
        log(f"the server at {a.url} did not answer")
        return 1
    folder = Folder(a.dir, a.url, a.key)
    for _ in range(3):                       # a file must be seen unchanged on two polls
        folder.poll()
        time.sleep(1.2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
