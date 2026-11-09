# SmartFuel

The `smart_fuel` package contains Django project configuration. Application
packages such as `truckstops` live alongside it at the repository root.

## Database setup

With the project's Python environment activated:

```powershell
python manage.py migrate
python manage.py import_fuel_prices "data\fuel-prices-for-be-assessment.csv"
```

Keep the path quoted: Bash/Git Bash treats unquoted backslashes as escape
characters.

The `TruckStop` model stores source identifiers, address, country, price,
optional coordinates, and the last database update time. Prices retain eight
decimal places. The update time is not a source-price timestamp: the CSV
provides no timestamps, fuel types, currency, or volume-unit metadata.

### Import policy

- OPIS ID uniquely identifies a station. The supplied file has 8,151 rows and
  6,738 distinct IDs.
- Repeated IDs retain the lowest listed price and the associated row's name.
  The first row wins equal-price ties. Conflicting names/prices and duplicate
  row counts are reported. This assumes prices for a station are comparable;
  the source does not establish that.
- Conflicting address, city, state/province, or rack for an ID aborts the import.
- Both US and Canadian stations are retained. Country is derived from the
  state/province code; future USA routing must filter by `country="US"`.
  Do not compare Canadian and US prices without verified currency/unit metadata.
- Whitespace is stripped, state/province codes are uppercased, and invalid
  records, unknown regions, empty files, and malformed CSVs fail explicitly.
- Validation finishes before any database writes. Writes are transactional;
  existing stations are updated, missing stations are inserted, and stations
  absent from a later file are not deleted.
- Unchanged records retain their update time. Imports preserve coordinates.
  If a station's source address changes, review its coordinates before routing.

Import is an explicit management command, not startup behavior or a data
migration. Geocoding is a separate future enrichment step: coordinates remain
null until verified, and this importer makes no network calls.

Truck stops are also available in Django admin using an administrator account.

## Validation

```powershell
python manage.py test truckstops
python manage.py makemigrations --check --dry-run
```
