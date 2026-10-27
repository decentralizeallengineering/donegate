import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "donegate.py")
FIX = os.path.join(HERE, "fixtures")


def run_hook(fixture, final, **extra):
    event = {"transcript_path": os.path.join(FIX, fixture), "last_assistant_message": final}
    event.update(extra)
    env = dict(os.environ, DONEGATE_CONFIG=os.path.join(HERE, "..", "donegate.json"))
    env.pop("DONEGATE_OFF", None)
    proc = subprocess.run([sys.executable, HOOK], input=json.dumps(event), capture_output=True,
                          text=True, timeout=20, env=env, cwd=HERE, check=False)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout) if proc.stdout.strip() else None


class DonegateTests(unittest.TestCase):
    def test_blocks_claim_without_test_after_edit(self):
        out = run_hook("no_test.jsonl", "Fixed. The bug is done.")
        self.assertEqual(out["decision"], "block")
        self.assertIn("no test or check command ran", out["reason"])

    def test_allows_claim_after_read_test_output(self):
        self.assertIsNone(run_hook("tested.jsonl", "Fixed, all 3 tests pass."))
        self.assertIsNone(run_hook("no_edit.jsonl", "It works out to 4."))

    def test_blocks_when_test_output_never_read(self):
        out = run_hook("unread.jsonl", "Fixed, tests are running.")
        self.assertEqual(out["decision"], "block")
        self.assertIn("never read", out["reason"])

    def test_unverified_line_is_the_way_out(self):
        final = "Fixed the parser.\nUNVERIFIED: the retry path because no network in this sandbox"
        self.assertIsNone(run_hook("no_test.jsonl", final))

    def test_brevity_blocks_once_per_stop(self):
        long_final = "word " * 150
        out = run_hook("tested.jsonl", long_final)
        self.assertEqual(out["decision"], "block")
        self.assertIn("shorten: details go to a file", out["reason"])
        self.assertIsNone(run_hook("tested.jsonl", long_final, stop_hook_active=True))
        # a second, claim-based block is also suppressed on the retry stop
        self.assertIsNone(run_hook("no_test.jsonl", "Fixed. The bug is done.", stop_hook_active=True))


if __name__ == "__main__":
    unittest.main()
