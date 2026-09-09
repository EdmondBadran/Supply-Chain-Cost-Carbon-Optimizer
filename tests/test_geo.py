"""Tests for the city lookup, mostly about spelling.

The reference table is an ASCII GeoNames extract and it is not consistent
about how it transliterates: Malmoe keeps the umlaut as a digraph, Vaesteras
does it for the a-umlaut and drops the ring, Tromso drops the slash, Zuerich
expands it. A file exported from a Swedish, Danish or German system spells all
of those properly and used to miss every one, silently reporting a bad row for
a city that is plainly in the table.

That is not a hypothetical: it is what happened the first time this was run
against a Swedish dataset. So the accented names are pinned by name here, and
so is the direction that matters more, which is that the ASCII spellings never
stopped working.

Run with: python -m unittest discover tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from optimizer import geo


class Spelling(unittest.TestCase):
    def assertSamePlace(self, first, second, country):
        a = geo.locate(first, country)
        b = geo.locate(second, country)
        self.assertEqual(a, b, f"{first} and {second} should be the same place")

    def test_swedish_names_resolve_however_they_are_written(self):
        """Each of these folds differently in the reference table, which is
        why they are listed one by one rather than tested as a rule."""
        for local, ascii_form in [
            ("Malmö", "Malmoe"),
            ("Jönköping", "Joenkoeping"),
            ("Västerås", "Vaesteras"),
            ("Linköping", "Linkoeping"),
            ("Norrköping", "Norrkoeping"),
            ("Örebro", "OErebro"),
            ("Gävle", "Gaevle"),
            ("Umeå", "Umea"),
            ("Luleå", "Lulea"),
        ]:
            with self.subTest(city=local):
                self.assertSamePlace(local, ascii_form, "SE")

    def test_danish_and_norwegian_names_resolve(self):
        self.assertSamePlace("Århus", "Arhus", "DK")
        self.assertSamePlace("Tromsø", "Tromso", "NO")

    def test_german_and_swiss_names_resolve(self):
        self.assertSamePlace("Zürich", "Zuerich", "CH")
        self.assertSamePlace("Köln", "Koeln", "DE")
        self.assertSamePlace("Düsseldorf", "Duesseldorf", "DE")

    def test_a_local_name_finds_the_english_one(self):
        """Göteborg and Gothenburg are different words, not different
        spellings, so folding alone will never bridge them."""
        self.assertSamePlace("Göteborg", "Gothenburg", "SE")
        self.assertSamePlace("København", "Copenhagen", "DK")
        self.assertSamePlace("Helsingfors", "Helsinki", "FI")
        self.assertSamePlace("Wien", "Vienna", "AT")
        self.assertSamePlace("München", "Munich", "DE")

    def test_an_exonym_never_shadows_a_name_the_table_already_has(self):
        """The table keeps Munich but also keeps Koeln, so the exonym map has
        to be an extra candidate rather than a replacement. Mapping Köln to
        Cologne, which is not in the table, must not lose Koeln, which is."""
        self.assertIsNotNone(geo.locate("Köln", "DE"))

    def test_plain_ascii_names_still_work(self):
        """The direction that would be easy to break while fixing the other
        one, and the one every existing dataset depends on."""
        for city, country in [
            ("Stockholm", "SE"),
            ("Rotterdam", "NL"),
            ("Shenzhen", "CN"),
            ("Memphis", "US"),
            ("Dubai", "AE"),
            ("Sydney", "AU"),
            ("Bristol", "GB"),
        ]:
            with self.subTest(city=city):
                self.assertIsNotNone(geo.locate(city, country))

    def test_case_and_padding_do_not_matter(self):
        self.assertSamePlace("  mAlMö  ", "Malmö", "SE")

    def test_a_country_still_narrows_the_answer(self):
        """Folding widens what matches, so this is the guard that it did not
        widen far enough to start ignoring the country column."""
        self.assertNotEqual(
            geo.locate("Paris", "FR"), geo.locate("Paris", "US")
        )

    def test_an_unknown_city_is_still_an_error(self):
        with self.assertRaises(geo.GeocodeError):
            geo.locate("Nowhereville", "SE")
        with self.assertRaises(geo.GeocodeError):
            geo.locate("")

    def test_a_real_city_in_the_wrong_country_is_an_error(self):
        with self.assertRaises(geo.GeocodeError):
            geo.locate("Malmö", "JP")


class Folding(unittest.TestCase):
    def test_both_spellings_of_each_character_are_offered(self):
        variants = geo._fold_variants("ö")
        self.assertIn("oe", variants)
        self.assertIn("o", variants)

    def test_the_variant_count_is_capped(self):
        """A name of nothing but accented characters must not produce two to
        the power of its length."""
        self.assertLessEqual(
            len(geo._fold_variants("öäüåøæ" * 4)), geo.MAX_VARIANTS
        )

    def test_an_unaccented_name_produces_one_variant(self):
        self.assertEqual(geo._fold_variants("stockholm"), ["stockholm"])


class Distance(unittest.TestCase):
    def test_a_known_distance_is_about_right(self):
        """Stockholm to Gothenburg is roughly 400 km great circle."""
        stockholm = geo.locate("Stockholm", "SE")
        gothenburg = geo.locate("Göteborg", "SE")
        km = geo.distance_km(*stockholm, *gothenburg)
        self.assertGreater(km, 350)
        self.assertLess(km, 450)

    def test_a_place_is_no_distance_from_itself(self):
        point = geo.locate("Malmö", "SE")
        self.assertAlmostEqual(geo.distance_km(*point, *point), 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
