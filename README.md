# donegate

[![tests](https://github.com/decentralizeallengineering/donegate/actions/workflows/tests.yml/badge.svg)](https://github.com/decentralizeallengineering/donegate/actions/workflows/tests.yml) ![license](https://img.shields.io/badge/license-Apache--2.0-blue)

donegate is a Claude Code Stop hook that stops the agent from saying "done" after editing files unless it ran a test or check afterwards and read the output.

## Why

- An agent can edit a file, never run anything, and still end its turn with "Fixed, all tests pass."
- A test can be started in the background or with its output thrown away, so nothing was actually read, yet the final message sounds verified.
- Long final messages bury the one fact you need. The hook can also send an over-long message back once with a request to shorten it.

## Quick start

One file, standard library only, Python 3.10 or newer. From a checkout of this repo, feed the hook an event by hand (the transcripts are invented fixtures):

```sh
echo '{"transcript_path":"tests/fixtures/no_test.jsonl","last_assistant_message":"Fixed. The bug is done."}' | python3 donegate.py
echo '{"transcript_path":"tests/fixtures/tested.jsonl","last_assistant_message":"Fixed, all 3 tests pass."}' | python3 donegate.py
printf '{"transcript_path":"tests/fixtures/no_test.jsonl","last_assistant_message":"Fixed the parser.\\nUNVERIFIED: retry path no network here"}' | python3 donegate.py
python3 -m pytest -q
```

Expected: the first command prints a JSON block decision, `{"decision": "block", "reason": "Completion claimed but files were edited but no test or check command ran after the last edit. Run the test or check and read its output, or add a line 'UNVERIFIED: <what> <why>'."}`. The second and third print nothing, which means the stop is allowed. Pytest reports `5 passed`.

## Hook setup

Add a Stop hook to `.claude/settings.json` (project) or `~/.claude/settings.json` (user), with the real path to `donegate.py`:

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "python3 /path/to/donegate.py" }
        ]
      }
    ]
  }
}
```

The hook reads one JSON object on stdin and uses three fields: `transcript_path` (the session JSONL), `last_assistant_message` (the final message; if it is absent the last assistant text in the transcript is used) and `stop_hook_active`. To block, it prints `{"decision": "block", "reason": "..."}` on stdout; to allow, it prints nothing. It always exits 0.

Settings, first file found wins: `$DONEGATE_CONFIG`, `./.claude/donegate.json`, `$XDG_CONFIG_HOME/donegate/config.json` (default `~/.config/donegate/config.json`), then the `donegate.json` next to the script, which this repo ships. Keys in the file replace the built-in key of the same name as a whole (lists are not merged); keys you leave out keep the built-in default. Set `DONEGATE_OFF=1` to switch the hook off.

## How it decides

1. If `stop_hook_active` is true the hook allows the stop. It blocks at most once per stop, so it cannot loop the session.
2. If the final message matches a claim pattern (done, fixed, finished, completed, resolved, implemented, shipped, verified, works, working, passes, passed, passing, all green, good to go) and has no line of the form `UNVERIFIED: <what> <why>`, it looks at the transcript. If there is no edit (`Edit`, `Write`, `MultiEdit`, `NotebookEdit`), the claim is fine. Otherwise it needs a `Bash` command after the last edit that matches a test or check pattern (pytest, `python -m unittest|mypy|ruff|flake8`, ruff, eslint, tsc, shellcheck, `npm|pnpm|yarn test|lint|build`, `cargo test|check|clippy|build`, `go test|vet|build`, `make test|check|lint|verify`, and in the shipped `donegate.json` also `bash -n`, `npx vitest|jest|playwright`, and scripts named `test*.py`, `check*.sh` and similar) and does not match an ignore pattern (`--help`, `--version`, `--collect-only`). That command's output must have come back, be non-empty, not be a background run and not be redirected to `/dev/null`.
3. If the message is longer than `word_limit` words (default 100; 0 turns it off), the hook also blocks and asks for a shorter message with details moved to a file. This check does not depend on a claim, and `UNVERIFIED:` does not waive it.
4. Any error, such as unreadable input or a bad config file, allows the stop.

## Limits

- It matches words and command text, not meaning. A claim phrased in words the patterns do not contain passes, and a message that merely contains "works" or "done" can be blocked.
- It checks that a test or check command ran and produced output, not that it passed. A failing test run that was read still satisfies it.
- It only sees edits made with the edit tools. A file changed by a `Bash` command such as `sed -i` does not count as an edit.
- The command patterns decide what counts as a test. A project with its own test runner needs its pattern added to the config.
- It looks at the whole transcript for the last edit, not only the current turn, and it counts a matching command whatever it was run against.
- The `UNVERIFIED:` line is an honor-system exit: the hook checks only that the line has a subject and a reason.
- Failing open is deliberate, so a hook bug never wedges a session, but it means a broken setup looks the same as a passing one. The quick-start commands above are the way to check yours.
- Tested here against the transcript format in `tests/fixtures`; the field names above are what the code reads. It has not been run inside a live Claude Code session.

## License

Apache-2.0. See `LICENSE`.
