"""Checks for repository supply-chain rules that should survive future edits."""

from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALLOWED_PACKAGES = {
    "setuptools", "tzdata", "websockets", "anthropic", "openai", "keyring",
    "numpy", "scikit-learn",
}
REMOTE_ACTION = re.compile(r"^[^@\s]+@[0-9a-fA-F]{40}$")
USES_LINE = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)", re.MULTILINE)


def workflow_issues(source: str) -> list[str]:
    issues = []
    if re.search(r"^\s*pull_request_target\s*:", source, re.MULTILINE):
        issues.append("pull_request_target")
    if not re.search(r"^permissions:\s*$", source, re.MULTILINE):
        issues.append("missing permissions")
    for action in USES_LINE.findall(source):
        if not action.startswith("./") and not REMOTE_ACTION.fullmatch(action):
            issues.append(f"unlocked action: {action}")
    for line in source.splitlines():
        if re.search(r"\bpip\s+install\b", line) and "--require-hashes" not in line:
            issues.append("pip install without hashes")
    return issues


class RepositoryPolicyTests(unittest.TestCase):
    def test_every_declared_package_is_approved_and_bounded(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        requirements = list(project["build-system"]["requires"])
        requirements += project["project"].get("dependencies", [])
        for extra in project["project"].get("optional-dependencies", {}).values():
            requirements.extend(extra)
        for requirement in requirements:
            with self.subTest(requirement=requirement):
                package = requirement.partition(";")[0]
                match = re.fullmatch(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)(.*)", package)
                self.assertIsNotNone(match, requirement)
                name, version = match.groups()
                self.assertIn(name.lower().replace("_", "-"), ALLOWED_PACKAGES)
                self.assertNotIn("@", version, "direct URL dependency")
                self.assertRegex(version, r"(?:^|,)\s*<={0,1}\s*\d",
                                 "dependency needs an upper version bound")

    def test_workflows_keep_external_actions_and_installs_pinned(self) -> None:
        workflow_dir = ROOT / ".github" / "workflows"
        for path in list(workflow_dir.glob("*.yml")) + list(workflow_dir.glob("*.yaml")):
            with self.subTest(path=path.name):
                self.assertEqual(workflow_issues(path.read_text(encoding="utf-8")), [])

    def test_old_branch_workflow_would_be_rejected(self) -> None:
        source = "permissions:\n  contents: write\nsteps:\n  - uses: actions/checkout@v4\n  - run: pip install -e .\n"
        self.assertIn("unlocked action: actions/checkout@v4", workflow_issues(source))
        self.assertIn("pip install without hashes", workflow_issues(source))


if __name__ == "__main__":
    unittest.main()
