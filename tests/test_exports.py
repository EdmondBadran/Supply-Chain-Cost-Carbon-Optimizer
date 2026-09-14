"""The workbook writer, and the exports built on it.

The writer is small enough to check by reading, but what breaks a workbook
is what nobody reads for: a control character in an uploaded city name, a
column past Z, a formula written without its value. Excel refuses the whole
file over any of them and says so only by offering to repair it.

Run with: python -m unittest discover tests
"""

import io
import sys
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from optimizer import chain, exports, xlsx


def parts(book):
    with zipfile.ZipFile(io.BytesIO(book.to_bytes())) as archive:
        return {name: archive.read(name).decode("utf-8") for name in archive.namelist()}


class Writer(unittest.TestCase):
    def test_column_letters_run_past_z(self):
        self.assertEqual(
            [xlsx.column_letter(n) for n in (1, 26, 27, 52, 703)],
            ["A", "Z", "AA", "AZ", "AAA"],
        )

    def test_the_package_has_every_part_excel_needs(self):
        book = xlsx.Workbook(title="Test")
        book.add_sheet("Data").write(1, 1, "x")
        found = parts(book)
        for name in (
            "[Content_Types].xml",
            "_rels/.rels",
            "xl/workbook.xml",
            "xl/_rels/workbook.xml.rels",
            "xl/styles.xml",
            "xl/worksheets/sheet1.xml",
        ):
            with self.subTest(part=name):
                self.assertIn(name, found)

    def test_control_characters_never_reach_the_xml(self):
        book = xlsx.Workbook()
        book.add_sheet("Data").write(1, 1, "Malmö\x00\x07 depot")
        sheet = parts(book)["xl/worksheets/sheet1.xml"]
        self.assertIn("Malmö depot", sheet)
        self.assertNotIn("\x00", sheet)
        self.assertNotIn("\x07", sheet)

    def test_a_formula_is_written_with_its_value(self):
        book = xlsx.Workbook()
        book.add_sheet("Data").write(1, 1, xlsx.Formula("=SUM(A2:A3)", 5))
        self.assertIn("<f>SUM(A2:A3)</f><v>5</v>", parts(book)["xl/worksheets/sheet1.xml"])

    def test_text_that_looks_like_a_formula_stays_text(self):
        book = xlsx.Workbook()
        book.add_sheet("Data").write(1, 1, '=HYPERLINK("x")')
        sheet = parts(book)["xl/worksheets/sheet1.xml"]
        self.assertNotIn("<f>", sheet)
        self.assertIn('t="inlineStr"', sheet)

    def test_sheet_names_are_made_legal(self):
        sheet = xlsx.Workbook().add_sheet("Data/check: [every row]? and a long tail")
        self.assertLessEqual(len(sheet.name), 31)
        for character in "[]*?/\\:":
            self.assertNotIn(character, sheet.name)

    def test_filters_and_frozen_headings_are_declared(self):
        book = xlsx.Workbook()
        sheet = book.add_sheet("Routes")
        sheet.write(1, 1, "Heading")
        sheet.freeze(2, 1)
        sheet.autofilter(1, 1, 4, 3)
        found = parts(book)
        self.assertIn('<autoFilter ref="A1:C4"/>', found["xl/worksheets/sheet1.xml"])
        self.assertIn('state="frozen"', found["xl/worksheets/sheet1.xml"])
        self.assertIn("_xlnm._FilterDatabase", found["xl/workbook.xml"])

    def test_an_unknown_style_is_refused_when_written(self):
        with self.assertRaises(KeyError):
            xlsx.Workbook().add_sheet("Data").write(1, 1, "x", "no-such-style")


class Exports(unittest.TestCase):
    def test_the_carbon_unit_follows_the_size_of_the_network(self):
        """Kilograms where tonnes would round a small route to nothing,
        tonnes where kilograms would run to seven digits."""
        self.assertEqual(exports.carbon_unit(18_900)["label"], "kg")
        self.assertEqual(exports.carbon_unit(9_492_000)["label"], "t")

    def test_uploaded_text_cannot_run_as_a_formula_in_the_csv(self):
        self.assertEqual(exports._csv_text("=cmd()"), "'=cmd()")
        self.assertEqual(exports._csv_text("@SUM(A1)"), "'@SUM(A1)")
        self.assertEqual(exports._csv_text("Leeds"), "Leeds")

    def test_a_share_is_written_as_percentage_points_in_the_csv(self):
        unit = exports.carbon_unit(1000)
        self.assertEqual(exports._csv_value(0.253, "pct", unit), 25.3)
        self.assertEqual(exports._csv_value(1444.87, "usd", unit), 1445)
        self.assertEqual(exports._csv_value(None, "usd", unit), "")


class Pictured(unittest.TestCase):
    def test_stages_the_file_gives_nothing_to_are_left_out(self):
        """No supplier file means no suppliers and no inbound freight, and
        those are left out of the picture rather than drawn as zeros. A stage
        somebody added stays, because they put it there."""

        def stage(key, headline, cost=0.0, builtin=True):
            return {
                "key": key,
                "headline": headline,
                "cost": cost,
                "co2e": 0.0,
                "problem_count": 0,
                "builtin": builtin,
            }

        shown = chain.pictured(
            [
                stage("suppliers", 0),
                stage("inbound", 0),
                stage("warehousing", 1, 10.0),
                stage("assembly", None, builtin=False),
            ]
        )
        self.assertEqual([s["key"] for s in shown], ["warehousing", "assembly"])


if __name__ == "__main__":
    unittest.main()
