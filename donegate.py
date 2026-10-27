#!/usr/bin/env python3
"""donegate: a Claude Code Stop hook that enforces "done means tested".

Reads the hook JSON on stdin. When the final assistant message claims
completion, it blocks the stop (once) unless a test or check command ran after
the last file edit and its output came back. An explicit line
"UNVERIFIED: <what> <why>" is the way out. A second check blocks (once) when
the final message is longer than a word limit.

Config: JSON file, first found of $DONEGATE_CONFIG, ./.claude/donegate.json,
$XDG_CONFIG_HOME/donegate/config.json (default ~/.config/donegate/config.json),
then the donegate.json next to this script. Missing keys fall back to the
built-in defaults. Kill switch: DONEGATE_OFF=1. Any internal error fails open.
"""
import json
import os
import re
import sys

DEFAULTS = {
    "word_limit": 100,
    "test_command_patterns": [
        r"\bpytest\b",
        r"python[0-9.]*\s+-m\s+(pytest|unittest|mypy|ruff|flake8)\b",
        r"\b(ruff|flake8|mypy|pyright|pylint|shellcheck|eslint|tsc)\b",
        r"\b(npm|pnpm|yarn)\s+(run\s+)?(test|lint|build|typecheck|check)\b",
        r"\bcargo\s+(test|check|clippy|build)\b",
        r"\bgo\s+(test|vet|build)\b",
        r"\bmake\s+(test|check|lint|verify)\b",
    ],
    "ignore_command_patterns": [r"--help\b", r"--version\b", r"--collect-only\b"],
    "claim_patterns": [
        r"\b(done|fixed|finished|complete[d]?|resolved|implemented|shipped|verified)\b",
        r"\b(works|working|passes|passed|passing)\b",
        r"\ball (green|good|set|tests? pass\w*)\b",
        r"\bgood to go\b",
    ],
    "edit_tools": ["Edit", "Write", "MultiEdit", "NotebookEdit"],
}

UNVERIFIED_RE = re.compile(r"^\s*[-*>\s]*UNVERIFIED:\s*\S+\s+\S+", re.MULTILINE)
DISCARD_RE = re.compile(r"(>\s*/dev/null\b|&>\s*/dev/null\b)")


def load_config():
    """Merge the first config file found over the built-in defaults."""
    here = os.path.dirname(os.path.abspath(__file__))
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    candidates = [
        os.environ.get("DONEGATE_CONFIG"),
        os.path.join(".claude", "donegate.json"),
        os.path.join(xdg, "donegate", "config.json"),
        os.path.join(here, "donegate.json"),
    ]
    cfg = dict(DEFAULTS)
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    cfg.update(json.load(fh))
            except (OSError, ValueError):
                continue
            break
    return cfg


def _text_of(content):
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "tool_result":
                parts.append(_text_of(block.get("content")))
    return "\n".join(parts)


def read_transcript(path):
    """Return (steps, last_text). steps: ordered dicts for edits and commands."""
    steps, results, last_text = [], {}, ""
    try:
        fh = open(path, encoding="utf-8", errors="replace")  # noqa: SIM115
    except (OSError, TypeError):
        return steps, last_text
    with fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            msg = obj.get("message") or {}
            content = msg.get("content")
            if obj.get("type") == "user" and isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        results[block.get("tool_use_id")] = _text_of(block.get("content"))
            elif obj.get("type") == "assistant":
                text = _text_of(content) if not isinstance(content, str) else content
                if text.strip():
                    last_text = text
                for block in content if isinstance(content, list) else []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        steps.append({"id": block.get("id"), "name": block.get("name"),
                                      "input": block.get("input") or {}})
    for step in steps:
        step["result"] = results.get(step["id"])
    return steps, last_text


def _any(patterns, text):
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def check_verification(steps, cfg):
    """Return None when fine, else a short reason string."""
    last_edit = -1
    for i, step in enumerate(steps):
        if step["name"] in cfg["edit_tools"]:
            last_edit = i
    if last_edit < 0:
        return None
    runs = []
    for step in steps[last_edit + 1:]:
        if step["name"] != "Bash":
            continue
        cmd = step["input"].get("command", "")
        if _any(cfg["test_command_patterns"], cmd) and not _any(cfg["ignore_command_patterns"], cmd):
            runs.append(step)
    if not runs:
        return "files were edited but no test or check command ran after the last edit"
    for step in runs:
        cmd = step["input"].get("command", "")
        backgrounded = bool(step["input"].get("run_in_background"))
        if step["result"] is not None and step["result"].strip() and not backgrounded \
                and not DISCARD_RE.search(cmd):
            return None
    return "a test or check ran after the last edit but its output was never read"


def decide(event, cfg):
    """Return a block reason string or None."""
    if event.get("stop_hook_active"):
        return None
    steps, transcript_text = read_transcript(event.get("transcript_path"))
    final = event.get("last_assistant_message") or transcript_text or ""
    reasons = []
    claims = _any(cfg["claim_patterns"], final)
    if claims and not UNVERIFIED_RE.search(final):
        problem = check_verification(steps, cfg)
        if problem:
            reasons.append(
                "Completion claimed but " + problem + ". Run the test or check and read "
                "its output, or add a line 'UNVERIFIED: <what> <why>'.")
    limit = int(cfg.get("word_limit") or 0)
    words = len(final.split())
    if limit and words > limit:
        reasons.append(f"shorten: details go to a file (message was {words} words, limit {limit}).")
    return " ".join(reasons) or None


def main():
    if os.environ.get("DONEGATE_OFF"):
        return 0
    try:
        event = json.load(sys.stdin)
        reason = decide(event, load_config())
    except Exception:  # noqa: BLE001  fail open: never wedge a session
        return 0
    if reason:
        sys.stdout.write(json.dumps({"decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
