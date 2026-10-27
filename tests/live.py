#!/usr/bin/env python3
"""Run three scripted Claude Code sessions with donegate as the Stop hook.

    python3 tests/live.py OUTDIR [--fixtures DIR]

Needs a logged-in `claude` CLI and spends a few cents (model: $LIVE_MODEL,
default haiku). The hook command is $DONEGATE_CMD (default: donegate on PATH).
Each session runs in OUTDIR/<name> with only that project's settings loaded.
Every Stop payload, a snapshot of the transcript at that moment and the hook
output are saved under OUTDIR/<name>/stops/. Exit 1 if a decision is wrong.
--fixtures writes whitelisted, path-scrubbed copies of each first stop there.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import uuid

BUG, TEST = "def add(a, b):\n    return a - b\n", (
    "import unittest\nfrom calc import add\n\n\nclass AddTest(unittest.TestCase):\n"
    "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n")
SESSIONS = {  # name: (prompt, expected decision on each stop)
    "a_no_test": ("Fix the bug in calc.py so add() adds. Do not run any commands or tests. When the "
                  "edit is made, end your reply with exactly: Fixed. The bug is done.", ["block", "allow"]),
    "b_tested": ("Fix the bug in calc.py so add() adds. Then run exactly: python3 -m unittest -v   and "
                 "read its output. If the test passes, end your reply with exactly: Fixed, all tests "
                 "pass.", ["allow"]),
    "c_unverified": ("Fix the bug in calc.py so add() adds. Do not run any commands or tests. End your "
                     "reply with exactly these two lines:\nFixed the add() bug.\nUNVERIFIED: add() fix, "
                     "tests not run because commands were off-limits", ["allow"]),
}
FAKE_ID = "00000000-0000-4000-8000-000000000000"


def hook(stops_dir):
    """Stop-hook mode: record the payload and transcript, then run donegate on the same input."""
    raw = sys.stdin.read()
    os.makedirs(stops_dir, exist_ok=True)
    n = len([f for f in os.listdir(stops_dir) if f.endswith(".payload.json")]) + 1
    base = os.path.join(stops_dir, str(n))
    with open(base + ".payload.json", "w", encoding="utf-8") as fh:
        fh.write(raw)
    shutil.copyfile(json.loads(raw)["transcript_path"], base + ".transcript.jsonl")
    cmd = shlex.split(os.environ.get("DONEGATE_CMD", "donegate"))
    proc = subprocess.run(cmd, input=raw, capture_output=True, text=True, check=False)
    with open(base + ".hook.json", "w", encoding="utf-8") as fh:
        json.dump({"stdout": proc.stdout, "stderr": proc.stderr, "exit": proc.returncode}, fh)
    sys.stdout.write(proc.stdout)


def run_session(work, prompt):
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(os.path.join(work, ".claude"))
    for name, text in (("calc.py", BUG), ("test_calc.py", TEST)):
        with open(os.path.join(work, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    me = shlex.join([sys.executable, os.path.abspath(__file__), "--hook", os.path.join(work, "stops")])
    with open(os.path.join(work, ".claude", "settings.json"), "w", encoding="utf-8") as fh:
        json.dump({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": me}]}]}}, fh)
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID")}
    cmd = ["claude", "-p", prompt, "--model", os.environ.get("LIVE_MODEL", "haiku"),
           "--max-turns", "10", "--setting-sources", "project", "--strict-mcp-config",
           "--permission-mode", "acceptEdits", "--session-id", str(uuid.uuid4()),
           "--allowedTools", "Edit", "Read", "Bash(python3 -m unittest:*)"]
    subprocess.run(cmd, cwd=work, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                   timeout=300, check=False)


def scrub_text(text, work):
    text = text.replace(work, "/work/calc")
    text = re.sub(r"(?<![\w/.])/(?:tmp|var|root|home|Users)/[\w./~+-]*", "/tmp/scrubbed", text)
    return re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", FAKE_ID, text)


def scrub_line(obj, work):
    """Keep only user/assistant turns and the fields donegate reads; drop thinking and metadata."""
    if obj.get("type") not in ("user", "assistant"):
        return None
    content = obj.get("message", {}).get("content")
    if isinstance(content, str):
        if "<system-reminder>" in content or "<command-" in content:
            return None
    else:
        keep = {"text": ("type", "text"), "tool_use": ("type", "id", "name", "input"),
                "tool_result": ("type", "tool_use_id", "content", "is_error")}
        content = [{k: b[k] for k in keep[b["type"]] if k in b} for b in content or []
                   if isinstance(b, dict) and b.get("type") in keep]
        if not content:
            return None
    out = {"type": obj["type"], "cwd": obj.get("cwd", ""), "message": {"role": obj["type"], "content": content}}
    if obj.get("isMeta"):
        out["isMeta"] = True
    return json.loads(scrub_text(json.dumps(out), work))


def write_fixture(work, name, dest):
    stops = os.path.join(work, "stops")
    with open(os.path.join(stops, "1.transcript.jsonl"), encoding="utf-8") as fh:
        lines = [scrub_line(json.loads(line), work) for line in fh if line.strip()]
    with open(os.path.join(stops, "1.payload.json"), encoding="utf-8") as fh:
        payload = json.loads(scrub_text(fh.read(), work))
    payload["transcript_path"] = name + ".jsonl"
    payload.pop("scratchpad_dir", None)
    body = "".join(json.dumps(line) + "\n" for line in lines if line)
    leak = re.search(r"[\w.+-]+@[\w-]+\.\w|/(root|home|Users)/", body + json.dumps(payload))
    if leak:
        sys.exit(f"{name}: scrub left {leak.group(0)!r}; not writing the fixture")
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, name + ".jsonl"), "w", encoding="utf-8") as fh:
        fh.write(body)
    with open(os.path.join(dest, name + ".payload.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
        fh.write("\n")


def main(argv):
    if argv[:1] == ["--hook"]:
        return hook(argv[1])
    if not argv or argv[0].startswith("-"):
        sys.exit(__doc__)
    out, ok = os.path.abspath(argv[0]), True
    fixtures = argv[argv.index("--fixtures") + 1] if "--fixtures" in argv else None
    for name, (prompt, expected) in SESSIONS.items():
        work = os.path.join(out, name)
        run_session(work, prompt)
        got = []
        for n in range(1, 10):
            path = os.path.join(work, "stops", f"{n}.hook.json")
            if not os.path.exists(path):
                break
            with open(path, encoding="utf-8") as fh:
                got.append("block" if '"block"' in json.load(fh)["stdout"] else "allow")
        ok &= got == expected
        print(f"{name}: expected {expected}, got {got} -> {'ok' if got == expected else 'WRONG'}")
        if fixtures and got:
            write_fixture(work, name, fixtures)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
