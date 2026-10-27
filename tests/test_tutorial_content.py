"""The handbook stays consistent with the pages that open it.

A help button whose topic no chapter covers silently opens the first
chapter, and a feature row for a missing chapter never appears. These
checks read the JavaScript as text, like the frontend syntax test.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "quantarium" / "assets" / "app" / "js"
CONTENT = (APP / "tutorial" / "content.js").read_text(encoding="utf-8")


def chapters() -> dict[str, list[str]]:
    start, end = CONTENT.index("export const chapters"), CONTENT.index("export const topics")
    block = CONTENT[start:end]
    found = {}
    for match in re.finditer(r'\{ id: "([a-z-]+)".*?topics: \[([^\]]*)\] \}', block, re.DOTALL):
        found[match.group(1)] = re.findall(r'"([a-z-]+)"', match.group(2))
    return found


def topic_entries() -> set[str]:
    start, end = CONTENT.index("export const topics"), CONTENT.index("export const features")
    return set(re.findall(r'^  "([a-z-]+)": \[', CONTENT[start:end], re.MULTILINE))


def feature_chapters() -> list[str]:
    start = CONTENT.index("export const features")
    end = CONTENT.index("export function chapterForTopic")
    return re.findall(r'^  \["([a-z-]+)", ', CONTENT[start:end], re.MULTILINE)


def help_topics() -> set[str]:
    used = set()
    for path in APP.rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        used |= set(re.findall(r'HelpButton\} topic="([a-z-]+)"', text))
        for expression in re.findall(r'HelpButton\} topic=\$\{([^}]*)\}', text):
            used |= set(re.findall(r'"([a-z-]+)"', expression))
    return used


class TutorialContentTests(unittest.TestCase):
    def test_every_help_button_opens_the_chapter_for_its_topic(self) -> None:
        covered = {topic for topics in chapters().values() for topic in topics}
        missing = sorted(help_topics() - covered)
        self.assertEqual(missing, [], "help topics no chapter covers")

    def test_every_chapter_topic_has_an_entry(self) -> None:
        entries = topic_entries()
        for chapter, topics in chapters().items():
            with self.subTest(chapter=chapter):
                self.assertEqual(sorted(set(topics) - entries), [])

    def test_every_feature_names_a_chapter(self) -> None:
        names = set(chapters())
        self.assertGreater(len(names), 10)
        self.assertEqual(sorted(set(feature_chapters()) - names), [])

    def test_handbook_lab_chapter_is_numbered_before_feed(self) -> None:
        self.assertIn('title: "7 · The Lab · registered comparisons"', CONTENT)
        self.assertIn('title: "8 · Feed Batch practice"', CONTENT)

    def test_guided_tutorial_includes_the_lab_attempt_lesson(self) -> None:
        tour = (APP / "tutorial" / "tour.js").read_text(encoding="utf-8")
        self.assertIn('title: "Test it honestly"', tour)
        self.assertIn("tutorial_first_cell", tour)
        self.assertIn("already used related scenario data", tour)
        # The tutorial follows its Lab attempts by idea ID, so the form must not let it be changed.
        lab = (APP / "pages" / "lab.js").read_text(encoding="utf-8")
        self.assertIn("readOnly=${tutorial}", lab)


    def test_the_handbook_covers_the_v04_features(self) -> None:
        covered = {topic for topics in chapters().values() for topic in topics}
        for topic in ("lab", "supercells", "ask", "tape", "allocator", "model-memory", "disguise", "orders",
                      "model-card"):
            self.assertIn(topic, covered)


    def test_the_handbook_covers_the_referee(self) -> None:
        covered = {topic for topics in chapters().values() for topic in topics}
        for topic in ("referee", "decision-files", "forward-inbox", "deciders", "trials", "evidence-report"):
            self.assertIn(topic, covered)

    def test_the_handbook_covers_the_users_own_data(self) -> None:
        covered = {topic for topics in chapters().values() for topic in topics}
        for topic in ("research-data", "survivorship", "own-trades"):
            self.assertIn(topic, covered)


if __name__ == "__main__":
    unittest.main()
