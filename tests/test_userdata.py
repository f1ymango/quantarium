"""v0.9.0: research data in Quantarium's own format, and scenarios imported from it."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from quantarium.scenario import load_scenario
from quantarium.userdata import import_user_data, read_user_data

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "data" / "fictional_index"
SOURCE = {"format": "quantarium-data-v1", "supplier": "test", "survivorship": "survivors_only"}
HEADER = "date,symbol,open,high,low,close,volume\n"


def bar(day: int, symbol: str, price: float = 10) -> str:
    return f"2030-01-{day:02d},{symbol},{price},{price + 1},{price - 1},{price},100\n"


class UserDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name) / "data"
        self.folder.mkdir()
        (self.folder / "source.json").write_text(json.dumps(SOURCE), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def prices(self, *rows: str) -> None:
        (self.folder / "prices.csv").write_text(HEADER + "".join(rows), encoding="utf-8")

    def test_the_example_reads_with_its_late_listing_delistings_and_index(self) -> None:
        data = read_user_data(EXAMPLE)
        summary = data.summary()
        self.assertEqual((summary["symbols"], summary["sessions"]), (6, 160))
        self.assertEqual((summary["listed_late"], summary["delistings"]), (1, 2))
        self.assertEqual(summary["survivorship"], "survivorship_free")
        self.assertEqual(data.delistings["ECHO"].reason, "bankruptcy")
        self.assertEqual(len(data.membership["FIC5"]["CHRL"]), 2)
        self.assertEqual(summary["warnings"], [])

    def test_symbols_may_come_and_go_but_not_skip_a_session(self) -> None:
        self.prices(bar(7, "AAA"), bar(8, "AAA"), bar(8, "BBB"), bar(9, "AAA"), bar(9, "BBB"))
        self.assertEqual(read_user_data(self.folder).spans["BBB"][0].day, 8)
        self.prices(bar(7, "AAA"), bar(7, "BBB"), bar(8, "AAA"), bar(9, "AAA"), bar(9, "BBB"))
        with self.assertRaisesRegex(ValueError, "BBB has no bar on 2030-01-08"):
            read_user_data(self.folder)

    def test_a_stop_without_a_delisting_and_a_survivors_only_declaration_are_warned_about(self) -> None:
        self.prices(bar(7, "AAA"), bar(7, "BBB"), bar(8, "AAA"))
        warnings = read_user_data(self.folder).warnings
        self.assertTrue(any("BBB stops on 2030-01-07" in warning for warning in warnings), warnings)
        self.assertTrue(any("survivorship caveat" in warning for warning in warnings), warnings)

    def test_delistings_must_follow_the_last_bar(self) -> None:
        self.prices(bar(7, "AAA"), bar(7, "BBB"), bar(8, "AAA"))
        (self.folder / "delistings.csv").write_text("symbol,date,price,reason\nBBB,2030-01-07,9,x\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "still has a bar"):
            read_user_data(self.folder)
        (self.folder / "delistings.csv").write_text("symbol,date,price,reason\nBBB,2030-01-08,,merger\n", encoding="utf-8")
        self.assertIsNone(read_user_data(self.folder).delistings["BBB"].price)

    def test_overlapping_membership_is_refused(self) -> None:
        self.prices(bar(7, "AAA"))
        (self.folder / "membership.csv").write_text(
            "index,symbol,from,through\nX,AAA,2030-01-01,2030-03-01\nX,AAA,2030-02-01,\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "overlaps"):
            read_user_data(self.folder)

    def test_a_source_is_required_and_its_keys_are_checked(self) -> None:
        self.prices(bar(7, "AAA"))
        (self.folder / "source.json").unlink()
        with self.assertRaisesRegex(ValueError, "needs a source.json"):
            read_user_data(self.folder)
        (self.folder / "source.json").write_text(json.dumps({**SOURCE, "survivorship": "probably"}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "survivorship must be"):
            read_user_data(self.folder)
        (self.folder / "source.json").write_text(json.dumps({**SOURCE, "run": "x"}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unknown keys"):
            read_user_data(self.folder)

    def test_an_import_records_its_source_and_detects_changed_files(self) -> None:
        target = Path(self.temp.name) / "scenario"
        scenario, warnings = import_user_data(EXAMPLE, target, name="fic")
        self.assertEqual((scenario.kind, scenario.survivorship), ("historical", "survivorship_free"))
        self.assertEqual(scenario.provenance["method"], "import-data")
        self.assertIsNotNone(scenario.delistings_path)
        self.assertIsNotNone(scenario.membership_path)
        self.assertEqual(warnings, [])
        with (target / "membership.csv").open("a", encoding="utf-8") as tampered:
            tampered.write("FIC5,ZZZ,2031-01-06,\n")
        with self.assertRaisesRegex(ValueError, "membership.csv is missing or changed"):
            load_scenario(target)
        with self.assertRaises(FileExistsError):
            import_user_data(EXAMPLE, target, name="fic")


if __name__ == "__main__":
    unittest.main()
