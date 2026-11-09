import csv
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase

from .management.commands.import_fuel_prices import CSV_FIELDS
from .models import TruckStop


class ImportFuelPricesTests(TestCase):
    def setUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "prices.csv"

    def write_csv(self, rows: list[list[str]]) -> None:
        with self.path.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(CSV_FIELDS)
            writer.writerows(rows)

    def row(self, **overrides: str) -> list[str]:
        values = {
            "opis_id": "7",
            "name": "  Test Truck Stop  ",
            "address": " I-44, EXIT 283 ",
            "city": " Big Cabin ",
            "state": "ok",
            "rack_id": "307",
            "retail_price": "3.00733333",
        }
        values.update(overrides)
        return [values[field] for field in CSV_FIELDS.values()]

    def run_import(self) -> tuple[str, str]:
        stdout, stderr = StringIO(), StringIO()
        call_command(
            "import_fuel_prices", self.path, stdout=stdout, stderr=stderr
        )
        return stdout.getvalue(), stderr.getvalue()

    def test_import_normalizes_fields_and_preserves_decimal_precision(self) -> None:
        self.write_csv([self.row()])
        stdout, _ = self.run_import()
        station = TruckStop.objects.get(opis_id=7)
        self.assertEqual(station.name, "Test Truck Stop")
        self.assertEqual(station.city, "Big Cabin")
        self.assertEqual(station.address, "I-44, EXIT 283")
        self.assertEqual(station.state, "OK")
        self.assertEqual(station.country, "US")
        self.assertEqual(station.rack_id, 307)
        self.assertEqual(station.retail_price, Decimal("3.00733333"))
        self.assertIsNone(station.latitude)
        self.assertIsNone(station.longitude)
        self.assertIn("created 1", stdout)

    def test_lowest_price_row_wins_and_conflicts_are_reported(self) -> None:
        self.write_csv([
            self.row(retail_price="4.00"),
            self.row(name="Cheaper station alias", retail_price="3.00"),
            self.row(retail_price="3.50"),
            self.row(name="Equal price alias", retail_price="3.00"),
        ])
        _, stderr = self.run_import()
        station = TruckStop.objects.get()
        self.assertEqual(station.retail_price, Decimal("3"))
        self.assertEqual(station.name, "Cheaper station alias")
        self.assertIn("Duplicate rows: 3", stderr)
        self.assertIn("IDs with conflicting prices: 1", stderr)
        self.assertIn("IDs with conflicting names: 1", stderr)

    def test_canadian_stations_are_retained_with_country(self) -> None:
        self.write_csv([
            self.row(state="CA"),
            self.row(opis_id="8", state="on"),
        ])
        stdout, _ = self.run_import()
        self.assertEqual(TruckStop.objects.get(opis_id=7).country, "US")
        self.assertEqual(TruckStop.objects.get(opis_id=8).country, "CA")
        self.assertIn("US=1, CA=1", stdout)

    def test_repeat_import_is_unchanged_and_preserves_coordinates(self) -> None:
        self.write_csv([self.row()])
        self.run_import()
        station = TruckStop.objects.get()
        station.latitude = Decimal("36.577000")
        station.longitude = Decimal("-95.221000")
        station.save()
        timestamp = station.updated_at
        stdout, _ = self.run_import()
        station.refresh_from_db()
        self.assertEqual(TruckStop.objects.count(), 1)
        self.assertEqual(station.updated_at, timestamp)
        self.assertEqual(station.latitude, Decimal("36.577000"))
        self.assertEqual(station.longitude, Decimal("-95.221000"))
        self.assertIn("created 0, updated 0, unchanged 1", stdout)

    def test_later_file_updates_price_even_when_higher_and_keeps_other_stations(self) -> None:
        self.write_csv([self.row(), self.row(opis_id="8")])
        self.run_import()
        station = TruckStop.objects.get(opis_id=7)
        station.latitude = Decimal("36.577000")
        station.longitude = Decimal("-95.221000")
        station.save()
        self.write_csv([self.row(retail_price="4.12345678")])
        stdout, _ = self.run_import()
        station.refresh_from_db()
        self.assertEqual(station.retail_price, Decimal("4.12345678"))
        self.assertEqual(station.latitude, Decimal("36.577000"))
        self.assertEqual(station.longitude, Decimal("-95.221000"))
        self.assertEqual(TruckStop.objects.count(), 2)
        self.assertIn("created 0, updated 1", stdout)

    def test_invalid_rows_do_not_partially_update_database(self) -> None:
        self.write_csv([self.row()])
        self.run_import()
        self.write_csv([
            self.row(retail_price="4"),
            self.row(opis_id="8"),
            self.row(opis_id="9", retail_price="not-a-price"),
        ])
        with self.assertRaisesMessage(CommandError, "Line 4"):
            self.run_import()
        self.assertEqual(TruckStop.objects.count(), 1)
        self.assertEqual(
            TruckStop.objects.get().retail_price, Decimal("3.00733333")
        )

    def test_invalid_field_values_are_rejected(self) -> None:
        invalid_values = [
            {"retail_price": value}
            for value in ("0", "-1", "NaN", "Infinity", "1.123456789", "10000")
        ] + [
            {"opis_id": "0"},
            {"opis_id": "abc"},
            {"rack_id": "-1"},
            {"state": "ZZ"},
            {"name": " "},
            {"city": "x" * 101},
        ]
        for overrides in invalid_values:
            with self.subTest(overrides=overrides):
                self.write_csv([self.row(**overrides)])
                with self.assertRaises(CommandError):
                    self.run_import()
                self.assertFalse(TruckStop.objects.exists())

    def test_conflicting_locations_or_racks_are_rejected(self) -> None:
        for field in ("address", "city", "state", "rack_id"):
            value = {"state": "ON", "rack_id": "308"}.get(field, "different")
            with self.subTest(field=field):
                self.write_csv([self.row(), self.row(**{field: value})])
                with self.assertRaisesMessage(CommandError, "conflicting location"):
                    self.run_import()
                self.assertFalse(TruckStop.objects.exists())

    def test_invalid_files_are_rejected(self) -> None:
        contents = [
            "",
            ",".join(CSV_FIELDS) + "\n",
            "name,price\nStation,3\n",
            ",".join(CSV_FIELDS) + ",City\n",
            ",".join(CSV_FIELDS) + "\n7,missing,columns\n",
            ",".join(CSV_FIELDS) + "\n" + ",".join(self.row()) + ",extra\n",
            ",".join(CSV_FIELDS) + '\n"unterminated\n',
        ]
        for content in contents:
            with self.subTest(content=content):
                self.path.write_text(content, encoding="utf-8")
                with self.assertRaises(CommandError):
                    self.run_import()
                self.assertFalse(TruckStop.objects.exists())

    def test_missing_file_is_reported(self) -> None:
        with self.assertRaisesMessage(CommandError, "Unable to read CSV"):
            self.run_import()

    def test_database_enforces_price_and_coordinate_constraints(self) -> None:
        self.write_csv([self.row()])
        self.run_import()
        for values in (
            {"retail_price": 0},
            {"latitude": 10},
            {"latitude": 91, "longitude": 0},
            {"latitude": 0, "longitude": -181},
        ):
            with self.subTest(values=values):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    TruckStop.objects.update(**values)

    def test_supplied_dataset_imports_and_reimports(self) -> None:
        self.path = (
            settings.BASE_DIR / "data" / "fuel-prices-for-be-assessment.csv"
        )
        stdout, stderr = self.run_import()
        self.assertEqual(TruckStop.objects.count(), 6738)
        self.assertIn("Read 8151 rows", stdout)
        self.assertIn("IDs with conflicting prices: 597", stderr)
        self.assertIn("IDs with conflicting names: 227", stderr)
        stdout, _ = self.run_import()
        self.assertIn("created 0, updated 0, unchanged 6738", stdout)
