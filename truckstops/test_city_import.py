import csv
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase

from .management.commands.import_city_locations import ATTRIBUTION, SOURCE_URL
from .management.commands.import_fuel_prices import CSV_FIELDS
from .models import CityLocation, TruckStop

HEADERS = ("CITY", "COUNTY", "STATE_CODE", "LATITUDE", "LONGITUDE", "ID")


class ImportCityLocationsTests(TestCase):
    def setUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "us_cities.csv"
        self.city = CityLocation.objects.create(city="Big Cabin", state="OK", country="US")
        self.station = TruckStop.objects.create(
            opis_id=7, name="Test stop", address="I-44", city="Big Cabin",
            state="OK", country="US", rack_id=307, retail_price=3,
            city_location=self.city,
        )
        self.row = ["Big Cabin", "Craig", "OK", "36.607931", "-95.274736", "123"]

    def write_csv(self, rows: list[list[str]]) -> None:
        with self.path.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(HEADERS)
            writer.writerows(rows)

    def run_import(self, **options) -> tuple[str, str]:
        stdout, stderr = StringIO(), StringIO()
        call_command("import_city_locations", self.path, stdout=stdout, stderr=stderr, **options)
        return stdout.getvalue(), stderr.getvalue()

    def test_import_coordinates_and_provenance_without_network(self) -> None:
        self.write_csv([self.row])
        with patch("urllib.request.urlopen", side_effect=AssertionError("Unexpected network request")):
            stdout, _ = self.run_import()
        self.city.refresh_from_db()
        self.assertEqual(self.city.status, "resolved")
        self.assertEqual(self.city.latitude, Decimal("36.607931"))
        self.assertEqual(self.city.longitude, Decimal("-95.274736"))
        self.assertEqual(self.city.provider, SOURCE_URL)
        self.assertEqual(self.city.source_id, "123")
        self.assertEqual(self.city.attribution, ATTRIBUTION)
        self.assertEqual(self.city.precision, "city")
        self.assertIsNotNone(self.city.imported_at)
        self.station.refresh_from_db()
        self.assertIsNone(self.station.latitude)
        self.assertIn("resolved 1", stdout)

    def test_repeat_import_preserves_resolved_coordinates_and_timestamp(self) -> None:
        self.write_csv([self.row])
        self.run_import()
        self.city.refresh_from_db()
        timestamp = self.city.imported_at
        self.write_csv([self.row[:3] + ["37", "-96", "123"]])
        stdout, _ = self.run_import()
        self.city.refresh_from_db()
        self.assertEqual(self.city.latitude, Decimal("36.607931"))
        self.assertEqual(self.city.imported_at, timestamp)
        self.assertIn("preserved 1", stdout)

    def test_existing_resolved_results_are_not_replaced(self) -> None:
        CityLocation.objects.filter(pk=self.city.pk).update(
            status="resolved", latitude=36, longitude=-95, provider=SOURCE_URL, source_id="123"
        )
        self.write_csv([self.row])
        self.run_import()
        self.city.refresh_from_db()
        self.assertEqual(self.city.latitude, Decimal("36"))
        self.assertEqual(self.city.provider, SOURCE_URL)

    def test_duplicate_names_for_same_source_id_are_not_ambiguous(self) -> None:
        self.write_csv([self.row, self.row])
        self.run_import()
        self.city.refresh_from_db()
        self.assertEqual(self.city.status, "resolved")

    def test_blank_county_is_allowed(self) -> None:
        self.write_csv([[self.row[0], "", *self.row[2:]]])
        self.run_import()
        self.city.refresh_from_db()
        self.assertEqual(self.city.status, "resolved")

    def test_distinct_source_ids_are_ambiguous_even_with_same_coordinates(self) -> None:
        self.write_csv([self.row, self.row[:-1] + ["456"]])
        with self.assertRaisesMessage(CommandError, "remain unresolved"):
            self.run_import()
        self.city.refresh_from_db()
        self.assertEqual(self.city.status, "ambiguous")
        self.assertIsNone(self.city.latitude)
        self.assertIn("123, 456", self.city.last_error)

    def test_missing_city_reported_and_successful_matches_saved(self) -> None:
        missing = CityLocation.objects.create(city="Missing", state="OK", country="US")
        TruckStop.objects.filter(pk=self.station.pk).update(city_location=missing)
        TruckStop.objects.create(
            opis_id=8, name="Second", address="I-44", city="Big Cabin",
            state="OK", country="US", rack_id=307, retail_price=3,
            city_location=self.city,
        )
        self.write_csv([self.row])
        with self.assertRaisesMessage(CommandError, "successful matches were saved"):
            self.run_import()
        self.city.refresh_from_db()
        missing.refresh_from_db()
        self.assertEqual(self.city.status, "resolved")
        self.assertEqual(missing.status, "not_found")
        self.assertIsNone(missing.longitude)

    def test_whitespace_case_and_state_matching(self) -> None:
        self.write_csv([
            [" BIG   CABIN ", "Craig", "ok", "36", "-95", "123"],
            ["Big Cabin", "Other county", "TX", "32", "-96", "456"],
        ])
        self.run_import()
        self.city.refresh_from_db()
        self.assertEqual(self.city.source_id, "123")
        self.assertEqual(self.city.latitude, Decimal("36"))

    def test_replace_resolved_explicitly_switches_source_and_coordinates(self) -> None:
        CityLocation.objects.filter(pk=self.city.pk).update(
            status="resolved", latitude=36, longitude=-95,
            provider="https://simplemaps.com/data/us-cities", source_id="old",
        )
        self.write_csv([self.row])
        self.run_import(replace_resolved=True)
        self.city.refresh_from_db()
        self.assertEqual(self.city.latitude, Decimal("36.607931"))
        self.assertEqual(self.city.provider, SOURCE_URL)
        self.assertEqual(self.city.source_id, "123")

    def test_replace_resolved_clears_ambiguous_or_missing_locations(self) -> None:
        for rows, expected in (
            ([self.row, self.row[:-1] + ["456"]], "ambiguous"),
            ([["Other", "Craig", "OK", "36", "-95", "456"]], "not_found"),
        ):
            with self.subTest(expected=expected):
                CityLocation.objects.filter(pk=self.city.pk).update(
                    status="resolved", latitude=36, longitude=-95, source_id="old",
                )
                self.write_csv(rows)
                with self.assertRaises(CommandError):
                    self.run_import(replace_resolved=True)
                self.city.refresh_from_db()
                self.assertEqual(self.city.status, expected)
                self.assertIsNone(self.city.latitude)
                self.assertIsNone(self.city.longitude)
                self.assertEqual(self.city.source_id, "")

    def test_dry_run_replace_keeps_existing_result(self) -> None:
        CityLocation.objects.filter(pk=self.city.pk).update(
            status="resolved", latitude=36, longitude=-95, provider="old",
        )
        self.write_csv([self.row])
        self.run_import(replace_resolved=True, dry_run=True)
        self.city.refresh_from_db()
        self.assertEqual(self.city.latitude, Decimal("36"))
        self.assertEqual(self.city.provider, "old")

    def test_canadian_cities_are_not_processed(self) -> None:
        CityLocation.objects.filter(pk=self.city.pk).update(country="CA", state="ON")
        self.write_csv([self.row])
        stdout, _ = self.run_import()
        self.city.refresh_from_db()
        self.assertEqual(self.city.status, "pending")
        self.assertIn("resolved 0", stdout)

    def test_dry_run_does_not_save(self) -> None:
        self.write_csv([self.row])
        stdout, _ = self.run_import(dry_run=True)
        self.city.refresh_from_db()
        self.assertEqual(self.city.status, "pending")
        self.assertIsNone(self.city.latitude)
        self.assertIn("Dry run: resolved 1", stdout)

    def test_unmatched_city_is_retried_after_source_correction(self) -> None:
        CityLocation.objects.filter(pk=self.city.pk).update(status="not_found", last_error="No exact match")
        self.write_csv([self.row])
        self.run_import()
        self.city.refresh_from_db()
        self.assertEqual(self.city.status, "resolved")
        self.assertEqual(self.city.last_error, "")

    def test_normalization_and_unique_region(self) -> None:
        self.assertEqual(self.city.city, "big cabin")
        with self.assertRaises(IntegrityError), transaction.atomic():
            CityLocation.objects.create(city=" BIG   CABIN ", state="ok", country="us")

    def test_city_status_coordinate_constraint(self) -> None:
        for values in (
            {"status": "resolved"},
            {"latitude": 36, "longitude": -95},
            {"status": "resolved", "latitude": 91, "longitude": -95},
            {"status": "resolved", "latitude": 36, "longitude": -181},
        ):
            with self.subTest(values=values):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    CityLocation.objects.filter(pk=self.city.pk).update(**values)

    def test_import_shares_normalized_city_and_relinks_after_city_change(self) -> None:
        with self.path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.writer(output)
            writer.writerow(CSV_FIELDS)
            writer.writerows([
                [7, "Test stop", "I-44", "BIG  CABIN", "OK", 307, "3.00733333"],
                [8, "Other stop", "I-44", "Big Cabin", "OK", 307, "3.00733333"],
            ])
        call_command("import_fuel_prices", self.path, stdout=StringIO(), stderr=StringIO())
        self.assertEqual(CityLocation.objects.count(), 1)
        self.assertEqual(TruckStop.objects.filter(city_location=self.city).count(), 2)
        text = self.path.read_text().replace("BIG  CABIN", "Tulsa")
        self.path.write_text(text)
        call_command("import_fuel_prices", self.path, stdout=StringIO(), stderr=StringIO())
        self.station.refresh_from_db()
        self.assertEqual(self.station.city_location.city, "tulsa")
        self.assertEqual(TruckStop.objects.get(opis_id=8).city_location_id, self.city.pk)

    def test_invalid_source_rows_leave_database_unchanged(self) -> None:
        invalid = [
            self.row[:3] + ["NaN", "-95", "456"],
            self.row[:3] + ["91", "-95", "456"],
            self.row[:3] + ["36", "Infinity", "456"],
            self.row[:3] + ["36", "-181", "456"],
            ["Other", "Other", "ZZ", "36", "-95", "456"],
            ["", "", "OK", "36", "-95", "456"],
            self.row[:-1] + [""],
            self.row[:3] + ["37", "-95", "123"],
            self.row + ["extra"],
            self.row[:-1],
        ]
        for row in invalid:
            with self.subTest(row=row):
                self.write_csv([self.row, row])
                with self.assertRaises(CommandError):
                    self.run_import()
                self.city.refresh_from_db()
                self.assertEqual(self.city.status, "pending")
                self.assertIsNone(self.city.latitude)

    def test_empty_missing_or_malformed_files_are_reported(self) -> None:
        with self.assertRaisesMessage(CommandError, "Unable to read cities CSV"):
            self.run_import()
        for content in (
            "", ",".join(HEADERS) + "\n", "city,lat,lng\n",
            ",".join(HEADERS) + ",ID\n", ",".join(HEADERS) + '\n"unterminated',
        ):
            with self.subTest(content=content):
                self.path.write_text(content, encoding="utf-8")
                with self.assertRaises(CommandError):
                    self.run_import()

    def test_actual_dataset_coverage(self) -> None:
        path = settings.BASE_DIR / "data" / "us_cities.csv"
        if not path.exists():
            self.skipTest("US Cities Database CSV is not available locally.")
        call_command(
            "import_fuel_prices",
            settings.BASE_DIR / "data" / "fuel-prices-for-be-assessment.csv",
            stdout=StringIO(), stderr=StringIO(),
        )
        self.path = path
        with self.assertRaisesMessage(CommandError, "remain unresolved"):
            self.run_import()
        self.assertEqual(CityLocation.objects.filter(country="US", status="resolved").count(), 3724)
        self.assertEqual(CityLocation.objects.filter(country="US", status="ambiguous").count(), 78)
        self.assertEqual(CityLocation.objects.filter(country="US", status="not_found").count(), 6)
        self.assertEqual(TruckStop.objects.filter(country="US", city_location__status="resolved").count(), 6286)
        self.assertFalse(CityLocation.objects.filter(country="CA").exclude(status="pending").exists())
