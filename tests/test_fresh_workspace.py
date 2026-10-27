"""`quantarium serve --fresh`: a new, empty workspace for testing, and a notice when serving the source folder."""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from quantarium.__main__ import main
from quantarium.workspace import FRESH_ROOT_ENV, Workspace, fresh_workspace, is_source_checkout


ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP = ROOT / "runs" / "test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)


class FreshWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.parent = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def serve_with(self, *arguments: str) -> tuple[Path, str]:
        served = {}
        errors = io.StringIO()
        with mock.patch("quantarium.server.serve", lambda workspace, host, port: served.setdefault("path", workspace)), \
                mock.patch("sys.argv", ["quantarium", "serve", *arguments]), contextlib.redirect_stderr(errors), \
                contextlib.redirect_stdout(io.StringIO()):
            main()
        return Path(served["path"]), errors.getvalue()

    def test_each_fresh_workspace_is_new_and_empty(self) -> None:
        first, second = fresh_workspace(self.parent), fresh_workspace(self.parent)
        self.assertNotEqual(first, second)
        for path in (first, second):
            self.assertEqual(list(path.iterdir()), [])
            overview = Workspace(path).overview()
            self.assertEqual((overview["configs"], overview["scenarios"], overview["databases"]), ([], [], []))
        with mock.patch.dict(os.environ, {FRESH_ROOT_ENV: str(self.parent / "from-env")}):
            self.assertEqual(fresh_workspace().parent, (self.parent / "from-env").resolve())

    def test_serve_fresh_serves_a_new_folder(self) -> None:
        path, notice = self.serve_with("--fresh", "--fresh-root", str(self.parent))
        self.assertEqual(path.parent, self.parent.resolve())
        self.assertTrue(path.name.startswith("fresh-"))
        self.assertEqual(notice, "")

    def test_serving_the_source_folder_says_so(self) -> None:
        self.assertTrue(is_source_checkout(ROOT))
        self.assertFalse(is_source_checkout(self.parent))
        _, notice = self.serve_with("--workspace", str(ROOT))
        self.assertIn("serve --fresh", notice)
        _, quiet = self.serve_with("--workspace", str(self.parent))
        self.assertEqual(quiet, "")

    def test_fresh_and_workspace_together_are_refused(self) -> None:
        with self.assertRaises(SystemExit):
            self.serve_with("--fresh", "--workspace", str(self.parent))


if __name__ == "__main__":
    unittest.main()
