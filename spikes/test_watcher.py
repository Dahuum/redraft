"""test_watcher.py — the watched folder: drop a PDF in in/, take the edited one from out/.

The folder is a client of a real running server (uvicorn on a free port, with an API key), so it is
tested through the same HTTP path it uses in the container. What must hold:

  * a PDF with a match is edited, lands in out/ with its receipt, and the original moves to done/;
  * a PDF the rules do not touch is NEVER passed on as if corrected: it goes to failed/ with the
    reason (the calibration breaks the judge and shows the test would catch that);
  * a file still being copied in is left alone until it stops changing;
  * a server that is down or a key that is wrong leaves the file where it is — nothing is moved to
    failed/ for the server's fault — and the problem is reported once, not every poll;
  * strict mode needs every rule to match; the default does not;
  * lock files, hidden files and non-PDFs are ignored; same-name files do not overwrite each other.
"""
import contextlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(HERE, "..", "backend")
sys.path.insert(0, BACKEND)
warnings.filterwarnings("ignore")

import fitz  # noqa: E402
import watcher  # noqa: E402

FAIL = []
KEY = "k-live-0123456789abcdef"


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, ("  " + str(detail)[:300] if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def make_pdf(text):
    d = fitz.open()
    d.new_page(width=595, height=842).insert_text((70, 120), text, fontsize=12, fontname="helv")
    b = d.tobytes()
    d.close()
    return b


def text_of(path_or_bytes):
    d = fitz.open(stream=path_or_bytes, filetype="pdf") if isinstance(path_or_bytes, bytes) else fitz.open(path_or_bytes)
    t = "".join(p.get_text() for p in d)
    d.close()
    return t


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def drop(root, name, data):
    p = os.path.join(root, "in", name)
    with open(p, "wb") as f:
        f.write(data)
    old = time.time() - 10
    os.utime(p, (old, old))
    return p


def pump(folder, n=3):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for _ in range(n):
            folder.poll()
    return buf.getvalue()


def ls(root, sub):
    d = os.path.join(root, sub)
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def rules(root, obj):
    with open(os.path.join(root, "replace.json"), "w") as f:
        json.dump(obj, f)


PORT = free_port()
URL = f"http://127.0.0.1:{PORT}"
env = {k: v for k, v in os.environ.items() if not k.startswith(("REDRAFT_", "SUPABASE_"))}
env["REDRAFT_API_KEY"] = KEY
server = subprocess.Popen([sys.executable, "-m", "uvicorn", "api:app", "--host", "127.0.0.1", "--port", str(PORT),
                           "--log-level", "warning"], cwd=BACKEND, env=env,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    check("the server comes up", watcher.wait_for_server(URL, 60))

    with tempfile.TemporaryDirectory(prefix="redraft_watch_") as root:
        # ── judge(): the receipt is the only thing believed ──────────────────
        good = {"edited": 1, "status": "ok", "changes": [{"find": "a", "found": True, "edited": 1}]}
        check("judge: a clean edit is delivered", watcher.judge(good, False)[0] is True)
        check("judge: nothing edited is not delivered", watcher.judge({"edited": 0, "status": "unchanged", "changes": []}, False)[0] is False)
        check("judge: a match with a problem is not delivered",
              watcher.judge({"edited": 1, "status": "partial", "changes": [{"find": "a", "found": True, "problems": [{"message": "x"}]}]}, False)[0] is False)
        check("judge: a match that did not read back is not delivered",
              watcher.judge({"edited": 1, "status": "ok", "changes": [{"find": "a", "found": True, "verified": False}]}, False)[0] is False)
        partial = {"edited": 1, "status": "partial", "changes": [{"find": "a", "found": True, "edited": 1}, {"find": "b", "found": False}]}
        check("judge: an unmatched rule does not hold a file back by default", watcher.judge(partial, False)[0] is True)
        check("judge: ... but does in strict mode", watcher.judge(partial, True)[0] is False)

        # ── replace.json ─────────────────────────────────────────────────────
        folder = watcher.Folder(root, URL, KEY)
        for sub in ("in", "out", "done", "failed"):
            check(f"the {sub}/ folder is created", os.path.isdir(os.path.join(root, sub)))
        try:
            watcher.load_rules(__import__("pathlib").Path(root))
            raised = False
        except ValueError:
            raised = True
        check("replace.json missing is an error that says so", raised)
        for label, body in [("not JSON", "{oops"), ("empty list", "[]"), ("no find", '[{"replace": "x"}]'),
                            ("find is empty", '[{"find": "", "replace": "x"}]'), ("replace missing", '[{"find": "x"}]')]:
            with open(os.path.join(root, "replace.json"), "w") as f:
                f.write(body)
            try:
                watcher.load_rules(__import__("pathlib").Path(root))
                bad = False
            except ValueError:
                bad = True
            check(f"replace.json {label}: refused", bad)
        rules(root, [{"find": "4821", "replace": "4822"}])
        ch, strict = watcher.load_rules(__import__("pathlib").Path(root))
        check("replace.json as a list: rules read, not strict", ch == [{"find": "4821", "replace": "4822"}] and strict is False)
        rules(root, {"changes": [{"find": "a", "replace": "b"}], "strict": True})
        check("replace.json as an object: strict read", watcher.load_rules(__import__("pathlib").Path(root))[1] is True)

        # ── a match is edited ────────────────────────────────────────────────
        rules(root, [{"find": "4821", "replace": "4822"}])
        src = make_pdf("Invoice 4821 total due")
        drop(root, "inv.pdf", src)
        log = pump(folder)
        check("a match: the edited PDF is in out/ with its receipt", ls(root, "out") == ["inv.pdf", "inv.receipt.json"], ls(root, "out"))
        check("a match: the original moved to done/, in/ is empty", ls(root, "done") == ["inv.pdf"] and ls(root, "in") == [])
        out_text = text_of(os.path.join(root, "out", "inv.pdf")) if "inv.pdf" in ls(root, "out") else ""
        check("a match: the new text is in the output and the old is gone", "4822" in out_text and "4821" not in out_text, out_text)
        check("a match: the original is kept byte for byte", open(os.path.join(root, "done", "inv.pdf"), "rb").read() == src)
        rc = json.load(open(os.path.join(root, "out", "inv.receipt.json"))) if "inv.receipt.json" in ls(root, "out") else {}
        check("a match: the receipt says ok and 1 edited", rc.get("status") == "ok" and rc.get("edited") == 1, rc)
        import hashlib
        check("a match: the receipt's sha256 is the output file's",
              (rc.get("sha256") or {}).get("after") == hashlib.sha256(open(os.path.join(root, "out", "inv.pdf"), "rb").read()).hexdigest())
        check("a match: it is reported in the log", "inv.pdf -> out/inv.pdf" in log, log)

        # ── same name again does not overwrite ───────────────────────────────
        drop(root, "inv.pdf", src)
        pump(folder)
        check("a second file with the same name does not overwrite the first",
              ls(root, "out") == ["inv-2.pdf", "inv-2.receipt.json", "inv.pdf", "inv.receipt.json"] and
              ls(root, "done") == ["inv-2.pdf", "inv.pdf"], (ls(root, "out"), ls(root, "done")))

        # ── no match: failed, never passed on ────────────────────────────────
        nomatch = make_pdf("Statement of account for March")
        drop(root, "stmt.pdf", nomatch)
        pump(folder)
        check("no match: nothing reaches out/", "stmt.pdf" not in ls(root, "out"), ls(root, "out"))
        check("no match: the original is in failed/ with the reason beside it",
              ls(root, "failed") == ["stmt.error.json", "stmt.pdf"], ls(root, "failed"))
        err = json.load(open(os.path.join(root, "failed", "stmt.error.json"))) if "stmt.error.json" in ls(root, "failed") else {}
        check("no match: the reason says what was not found", "4821" in err.get("reason", "") and "not found" in err.get("reason", ""), err)
        check("no match: the reason carries the receipt", isinstance(err.get("receipt"), dict))

        # ── a file still being copied in ─────────────────────────────────────
        half = len(src) // 2
        p = drop(root, "copying.pdf", src[:half])
        pump(folder, 1)
        with open(p, "ab") as f:
            f.write(src[half:])
        old = time.time() - 10
        os.utime(p, (old, old))
        pump(folder, 1)
        check("being copied: a file that changed between polls is left alone", ls(root, "in") == ["copying.pdf"] and "copying.pdf" not in ls(root, "out"))
        pump(folder, 2)
        check("being copied: once it stops changing it is edited whole",
              "copying.pdf" in ls(root, "out") and "4822" in text_of(os.path.join(root, "out", "copying.pdf")))
        fresh = os.path.join(root, "in", "fresh.pdf")
        open(fresh, "wb").write(src)
        pump(folder, 3)
        check("being copied: a file touched in the last second is not taken yet", "fresh.pdf" in ls(root, "in") and "fresh.pdf" not in ls(root, "out"))
        os.remove(fresh)
        open(os.path.join(root, "in", "empty.pdf"), "wb").close()
        os.utime(os.path.join(root, "in", "empty.pdf"), (time.time() - 10,) * 2)
        pump(folder, 3)
        check("being copied: an empty file is not taken", "empty.pdf" in ls(root, "in"))
        os.remove(os.path.join(root, "in", "empty.pdf"))

        # ── files that are not for it ────────────────────────────────────────
        for n in ("~$inv.pdf", ".hidden.pdf", "notes.txt", "scan.PNG"):
            drop(root, n, src)
        drop(root, "UPPER.PDF", src)
        pump(folder)
        check("ignored: lock files, hidden files and non-PDFs stay where they are",
              ls(root, "in") == sorted(["~$inv.pdf", ".hidden.pdf", "notes.txt", "scan.PNG"]), ls(root, "in"))
        check("ignored: ... and an upper-case .PDF is edited", "UPPER.PDF" in ls(root, "out"), ls(root, "out"))
        for n in ("~$inv.pdf", ".hidden.pdf", "notes.txt", "scan.PNG"):
            os.remove(os.path.join(root, "in", n))

        # ── garbage named .pdf ───────────────────────────────────────────────
        drop(root, "garbage.pdf", b"this is not a pdf at all, just text" * 10)
        pump(folder)
        check("garbage: a file that is not a PDF goes to failed/ with a reason",
              "garbage.pdf" in ls(root, "failed") and "garbage.error.json" in ls(root, "failed") and "garbage.pdf" not in ls(root, "out"),
              (ls(root, "failed"), ls(root, "out")))

        # ── strict mode ──────────────────────────────────────────────────────
        two = [{"find": "4821", "replace": "4822"}, {"find": "no such text", "replace": "x"}]
        rules(root, two)
        drop(root, "lenient.pdf", src)
        pump(folder)
        rc = json.load(open(os.path.join(root, "out", "lenient.receipt.json"))) if "lenient.receipt.json" in ls(root, "out") else {}
        check("default: a file where one of two rules matched is delivered, the receipt says partial",
              "lenient.pdf" in ls(root, "out") and rc.get("status") == "partial", (ls(root, "out"), rc.get("status")))
        rules(root, {"changes": two, "strict": True})
        drop(root, "strict.pdf", src)
        pump(folder)
        check("strict: the same file goes to failed/ because a rule matched nothing",
              "strict.pdf" in ls(root, "failed") and "strict.pdf" not in ls(root, "out"), (ls(root, "failed"), ls(root, "out")))

        # ── a bad replace.json is reported once and files wait ───────────────
        with open(os.path.join(root, "replace.json"), "w") as f:
            f.write("{oops")
        drop(root, "waiting.pdf", src)
        log = pump(folder, 5)
        check("bad rules: the file waits in in/", "waiting.pdf" in ls(root, "in") and "waiting.pdf" not in ls(root, "failed"))
        check("bad rules: the problem is reported once over five polls", log.count("not valid JSON") == 1, log)
        rules(root, [{"find": "4821", "replace": "4822"}])
        pump(folder, 2)
        check("bad rules: fixing replace.json lets the waiting file through", "waiting.pdf" in ls(root, "out"))

        # ── the server is down / the key is wrong ────────────────────────────
        down = watcher.Folder(root, f"http://127.0.0.1:{free_port()}", KEY)
        drop(root, "offline.pdf", src)
        log = pump(down, watcher.ATTEMPTS + 3)
        check("server down: the file stays in in/ however many polls (it is not the file's fault)",
              "offline.pdf" in ls(root, "in") and "offline.pdf" not in ls(root, "failed"), (ls(root, "in"), ls(root, "failed")))
        check("server down: it is said once", log.count("did not answer") == 1, log)
        up = watcher.Folder(root, URL, KEY)
        pump(up, 2)
        check("server down: when the server is back the file is edited", "offline.pdf" in ls(root, "out"))

        wrong = watcher.Folder(root, URL, "not-the-key")
        drop(root, "badkey.pdf", src)
        log = pump(wrong, 4)
        check("wrong key: the file stays in in/", "badkey.pdf" in ls(root, "in") and "badkey.pdf" not in ls(root, "failed"))
        check("wrong key: it is said once, and says what to set", log.count("refused the key") == 1 and "REDRAFT_API_KEY" in log, log)
        pump(up, 2)
        check("wrong key: with the right key the file is edited", "badkey.pdf" in ls(root, "out"))

        # ── calibration: a judge that waves everything through is caught ─────
        # Strict rules with one match: the server returns a PDF, only the judge can refuse it.
        real = watcher.judge
        watcher.judge = lambda receipt, strict: (True, "ok")
        try:
            rules(root, {"changes": two, "strict": True})
            drop(root, "calib.pdf", src)
            pump(watcher.Folder(root, URL, KEY), 3)
            slipped = "calib.pdf" in ls(root, "out") and "calib.pdf" not in ls(root, "failed")
        finally:
            watcher.judge = real
        check("calibration: with the judge broken, an unedited file reaches out/ (so the checks above can fail)", slipped)
finally:
    server.terminate()
    try:
        server.wait(10)
    except subprocess.TimeoutExpired:
        server.kill()

print("\nRESULT:", "FAIL " + str(FAIL) if FAIL else "all passed")
sys.exit(1 if FAIL else 0)
