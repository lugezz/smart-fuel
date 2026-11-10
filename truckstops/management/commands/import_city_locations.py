import csv
from argparse import ArgumentParser
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from ...models import US_STATES, CityLocation, normalize_city

SOURCE_URL = "https://github.com/kelvins/US-Cities-Database"
ATTRIBUTION = f"US Cities Database, Copyright (c) 2017 Kelvin S. do Prado, MIT License ({SOURCE_URL})"
REQUIRED_FIELDS = {"CITY", "COUNTY", "STATE_CODE", "LATITUDE", "LONGITUDE", "ID"}
UPDATE_FIELDS = [
    "latitude", "longitude", "status", "provider", "source_id",
    "attribution", "last_error", "imported_at",
]


@dataclass(frozen=True)
class CityRecord:
    source_id: str
    latitude: Decimal
    longitude: Decimal


class Command(BaseCommand):
    help = "Resolve existing US station cities offline from the kelvins/US-Cities-Database CSV."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("path", type=Path)
        parser.add_argument("--dry-run", action="store_true", help="Report matches without writing to the database.")
        parser.add_argument(
            "--replace-resolved", action="store_true",
            help="Re-evaluate resolved cities using this file, including clearing missing or ambiguous locations.",
        )

    def handle(self, *args, **options) -> None:
        index = self.read_index(options["path"])
        counts: Counter[str] = Counter()
        updated = []
        with transaction.atomic():
            cities = CityLocation.objects.filter(
                country="US", truckstops__isnull=False,
            ).distinct().order_by("pk")
            for city in cities:
                if city.status == CityLocation.Status.RESOLVED and not options["replace_resolved"]:
                    counts["preserved"] += 1
                    continue
                matches = index.get((normalize_city(city.city), city.state), {})
                self.apply_match(city, matches)
                counts[city.status] += 1
                if city.status != CityLocation.Status.RESOLVED:
                    self.stderr.write(f"{city.city}, {city.state}: {city.last_error}")
                updated.append(city)
            if not options["dry_run"]:
                CityLocation.objects.bulk_update(updated, UPDATE_FIELDS, batch_size=500)

        prefix = "Dry run" if options["dry_run"] else "Import complete"
        self.stdout.write(
            f"{prefix}: resolved {counts['resolved']}, preserved {counts['preserved']}, "
            f"ambiguous {counts['ambiguous']}, not found {counts['not_found']}."
        )
        if counts["ambiguous"] or counts["not_found"]:
            raise CommandError(
                "Some cities remain unresolved; successful matches were "
                + ("not saved (--dry-run)." if options["dry_run"] else "saved. Review the reported cities.")
            )

    @staticmethod
    def apply_match(city: CityLocation, matches: dict[str, CityRecord]) -> None:
        city.provider = SOURCE_URL
        city.attribution = ATTRIBUTION
        city.imported_at = timezone.now()
        city.latitude = city.longitude = None
        city.source_id = ""
        if not matches:
            city.status = CityLocation.Status.NOT_FOUND
            city.last_error = "No exact city/state match in US Cities Database."
        elif len(matches) > 1:
            city.status = CityLocation.Status.AMBIGUOUS
            city.last_error = f"Multiple US Cities Database IDs match: {', '.join(sorted(matches))}."
        else:
            record = next(iter(matches.values()))
            city.status = CityLocation.Status.RESOLVED
            city.latitude, city.longitude = record.latitude, record.longitude
            city.source_id = record.source_id
            city.last_error = ""

    @classmethod
    def read_index(cls, path: Path) -> dict[tuple[str, str], dict[str, CityRecord]]:
        index: dict[tuple[str, str], dict[str, CityRecord]] = {}
        records: dict[str, tuple[str, str, str, CityRecord]] = {}
        try:
            with path.open(encoding="utf-8-sig", newline="") as source:
                reader = csv.DictReader(source, strict=True)
                headers = reader.fieldnames
                if (
                    not headers or len(headers) != len(set(headers))
                    or not REQUIRED_FIELDS.issubset(headers)
                ):
                    raise CommandError(
                        "CSV must have unique headers including: " + ", ".join(sorted(REQUIRED_FIELDS))
                    )
                for row in reader:
                    cls.index_row(row, reader.line_num, index, records)
        except (OSError, UnicodeError, csv.Error) as exc:
            raise CommandError(f"Unable to read cities CSV: {exc}") from exc
        if not records:
            raise CommandError("Cities CSV contains no records.")
        return index

    @staticmethod
    def index_row(
        row: dict, line: int,
        index: dict[tuple[str, str], dict[str, CityRecord]],
        records: dict[str, tuple[str, str, str, CityRecord]],
    ) -> None:
        if None in row or any(value is None for value in row.values()):
            raise CommandError(f"Line {line}: incorrect number of columns.")
        values = {field: row[field].strip() for field in REQUIRED_FIELDS}
        if not all(value for field, value in values.items() if field != "COUNTY"):
            raise CommandError(f"Line {line}: required city fields must not be empty.")
        state = values["STATE_CODE"].upper()
        if state not in US_STATES | {"PR"}:
            raise CommandError(f"Line {line}: unrecognized state/territory {state!r}.")
        if len(values["ID"]) > 100:
            raise CommandError(f"Line {line}: source ID is too long.")
        try:
            latitude, longitude = Decimal(values["LATITUDE"]), Decimal(values["LONGITUDE"])
            if (
                not latitude.is_finite() or not longitude.is_finite()
                or not (-90 <= latitude <= 90 and -180 <= longitude <= 180)
            ):
                raise ValueError("Coordinates must be finite and in range.")
            record = CityRecord(
                values["ID"], latitude.quantize(Decimal("0.000001")),
                longitude.quantize(Decimal("0.000001")),
            )
        except (InvalidOperation, ValueError) as exc:
            raise CommandError(f"Line {line}: invalid coordinates: {exc}") from exc
        signature = (values["CITY"], values["COUNTY"], state, record)
        previous = records.get(record.source_id)
        if previous is not None and previous != signature:
            raise CommandError(f"Line {line}: conflicting rows for US Cities Database ID {record.source_id}.")
        records[record.source_id] = signature
        index.setdefault((normalize_city(values["CITY"]), state), {})[record.source_id] = record
