"""Run the Node-based verification-logic test as part of the Python suite.

The logic under test is JavaScript embedded in a shell script
(skills/plaud-upload/scripts/inject_upload.sh), so it is exercised by
tests/upload_verify_logic.test.mjs. This wrapper exists so a single
`python3 -m unittest discover -s tests` covers everything — nobody has to
remember a second command.

Node is already a hard requirement of this plugin (.mcp.json launches the
official MCP via npx, which needs Node >= 20), so depending on it here adds
no new burden. It is still skipped rather than failed when absent, matching
how the cache tests skip the ripgrep leg on machines without ripgrep.
"""

import pathlib
import shutil
import subprocess
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
NODE_TEST = REPO / "tests" / "upload_verify_logic.test.mjs"


class TestUploadVerifyLogic(unittest.TestCase):
    def test_verification_logic_scenarios(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node not on PATH — skipping upload-verification logic test")
        self.assertTrue(NODE_TEST.is_file(), f"missing {NODE_TEST}")

        proc = subprocess.run(
            [node, str(NODE_TEST)], capture_output=True, text=True, cwd=REPO
        )
        # Surface the node output on failure — otherwise a red test says nothing
        # about which scenario broke.
        self.assertEqual(
            proc.returncode,
            0,
            f"upload_verify_logic.test.mjs failed\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
