import csv
from argparse import ArgumentParser
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from ...models import (
    CANADIAN_PROVINCES,
    US_STATES,
    CityLocation,
    TruckStop,
    normalize_city,
)

CSV_FIELDS = {
    "OPIS Truckstop ID": "opis_id",
    "Truckstop Name": "name",
    "Address": "address",
    "City": "city",
    "State": "state",
    "Rack ID": "rack_id",
    "Retail Price": "retail_price",
}
IMPORT_FIELDS = (*CSV_FIELDS.values(), "country")
LOCATION_FIELDS = ("address", "city", "state", "country", "rack_id")


class Command(BaseCommand):
    help = (
        "Import US and Canadian truck stops, keeping the lowest listed price "
        "per OPIS ID. Re-imports update source fields without changing coordinates."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("path", type=Path, help="Path to the fuel-price CSV.")

    def handle(self, *args, **options) -> None:
        stations: dict[int, TruckStop] = {}
        price_conflicts: set[int] = set()
        name_conflicts: set[int] = set()
        row_count = 0
        try:
            with options["path"].open(encoding="utf-8-sig", newline="") as source:
                reader = csv.DictReader(source, strict=True)
                headers = reader.fieldnames
                if (
                    not headers
                    or len(headers) != len(set(headers))
                    or not set(CSV_FIELDS).issubset(headers)
                ):
                    raise CommandError(
                        "CSV must have unique headers including: "
                        + ", ".join(CSV_FIELDS)
                    )
                for row in reader:
                    row_count += 1
                    station = self.parse_row(row, reader.line_num)
                    previous = stations.get(station.opis_id)
                    if previous is None:
                        stations[station.opis_id] = station
                        continue
                    if any(
                        getattr(previous, field) != getattr(station, field)
                        for field in LOCATION_FIELDS
                    ):
                        raise CommandError(
                            f"Line {reader.line_num}: conflicting location or rack "
                            f"for OPIS ID {station.opis_id}."
                        )
                    if previous.retail_price != station.retail_price:
                        price_conflicts.add(station.opis_id)
                    if previous.name != station.name:
                        name_conflicts.add(station.opis_id)
                    # Keep the entire lowest-price row; the first row wins ties.
                    if station.retail_price < previous.retail_price:
                        stations[station.opis_id] = station
        except (OSError, UnicodeError, csv.Error) as exc:
            raise CommandError(f"Unable to read CSV: {exc}") from exc

        if not stations:
            raise CommandError("CSV contains no station records.")

        created: list[TruckStop] = []
        updated: list[TruckStop] = []
        with transaction.atomic():
            self.link_cities(stations)
            existing = TruckStop.objects.in_bulk(stations, field_name="opis_id")
            for opis_id, station in stations.items():
                saved = existing.get(opis_id)
                if saved is None:
                    created.append(station)
                elif any(
                    getattr(saved, field) != getattr(station, field)
                    for field in (*IMPORT_FIELDS, "city_location_id")
                ):
                    for field in (*IMPORT_FIELDS, "city_location_id"):
                        setattr(saved, field, getattr(station, field))
                    saved.updated_at = timezone.now()
                    updated.append(saved)
            TruckStop.objects.bulk_create(created, batch_size=500)
            TruckStop.objects.bulk_update(
                updated, [*IMPORT_FIELDS, "city_location", "updated_at"], batch_size=500
            )

        self.stdout.write(self.style.SUCCESS(
            f"Read {row_count} rows; {len(stations)} unique stations; "
            f"created {len(created)}, updated {len(updated)}, "
            f"unchanged {len(stations) - len(created) - len(updated)}."
        ))
        self.stdout.write(
            f"Countries: US={sum(s.country == 'US' for s in stations.values())}, "
            f"CA={sum(s.country == 'CA' for s in stations.values())}."
        )
        self.stderr.write(
            f"Duplicate rows: {row_count - len(stations)}; "
            f"IDs with conflicting prices: {len(price_conflicts)}; "
            f"IDs with conflicting names: {len(name_conflicts)}. "
            "Lowest price retained; first row wins equal-price ties."
        )

    @staticmethod
    def link_cities(stations: dict[int, TruckStop]) -> None:
        city_locations = {
            (location.city, location.state, location.country): location
            for location in CityLocation.objects.all()
        }
        for station in stations.values():
            key = (normalize_city(station.city), station.state, station.country)
            if key not in city_locations:
                city_locations[key], _ = CityLocation.objects.get_or_create(
                    city=key[0], state=key[1], country=key[2]
                )
            station.city_location = city_locations[key]

    @staticmethod
    def parse_row(row: dict, line: int) -> TruckStop:
        if None in row or any(value is None for value in row.values()):
            raise CommandError(f"Line {line}: incorrect number of columns.")
        values = {field: row[header].strip() for header, field in CSV_FIELDS.items()}
        state = values["state"].upper()
        if state in US_STATES:
            country = TruckStop.Country.USA
        elif state in CANADIAN_PROVINCES:
            country = TruckStop.Country.CANADA
        else:
            raise CommandError(f"Line {line}: unrecognized state/province {state!r}.")
        try:
            station = TruckStop(
                opis_id=int(values["opis_id"]),
                name=values["name"],
                address=values["address"],
                city=values["city"],
                state=state,
                country=country,
                rack_id=int(values["rack_id"]),
                retail_price=Decimal(values["retail_price"]),
            )
            station.full_clean(validate_unique=False, validate_constraints=False)
        except (ValueError, InvalidOperation, ValidationError) as exc:
            raise CommandError(f"Line {line}: invalid station data: {exc}") from exc
        return station
