# SmartFuel

The `smart_fuel` package contains Django project configuration. Application
packages such as `truckstops` live alongside it at the repository root.

## Database setup

With the project's Python environment activated:

```powershell
python manage.py migrate
python manage.py import_fuel_prices "data\fuel-prices-for-be-assessment.csv"
python manage.py import_city_locations "data\us_cities.csv"
```

Keep the path quoted: Bash/Git Bash treats unquoted backslashes as escape
characters.

The `TruckStop` model stores source identifiers, address, country, price,
optional coordinates, and the last database update time. Prices retain eight
decimal places. The update time is not a source-price timestamp: the CSV
provides no timestamps, fuel types, currency, or volume-unit metadata.

### Fuel-price import result

Initial import into an empty database:

```text
Read 8151 rows; 6738 unique stations; created 6738, updated 0, unchanged 0.
Countries: US=6626, CA=112.
Duplicate rows: 1413; IDs with conflicting prices: 597; IDs with conflicting names: 227. Lowest price retained; first row wins equal-price ties.
```

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
migration. Offline city import is a separate enrichment step; this importer makes no
network calls. It links stations to persistent city records.

Truck stops are also available in Django admin using an administrator account.

## City-level geolocation

For this assessment, we use city-level geolocation rather than attempt
to resolve every truck stop's highway-exit address. This is an implementation
assumption, not a requirement explicitly stated in the assignment.

- Resolve each unique normalized `(city, state, country)` once and persist the
  result for reuse across imports and application restarts.
- Stations in the same city share a `CityLocation` record and its coordinates.
  These are approximate locations, not verified station coordinates.
- City-coordinate import runs as a separate setup command, not during route
  optimization requests. Missing or ambiguous matches remain unresolved
  and are reported; they must not be assigned fabricated coordinates.
- City records store precision (`city`), provider URL, source record identifier,
  attribution, import time, status, and match errors. The station's own latitude and
  longitude remain separate and are not overwritten with city coordinates.
- USA route planning must use only US records with resolved city locations.
- A city marker does not establish that a station is on the route or reachable
  within 500 driving miles. Station access distances, detours, range feasibility,
  and fuel costs based on those distances are estimates. This approach cannot
  guarantee real-world minimum cost or exact station-to-station reachability.

### Offline city coordinates (default)

The city dataset is
[kelvins/US-Cities-Database](https://github.com/kelvins/US-Cities-Database),
provided as [`data/us_cities.csv`](data/us_cities.csv). The original CSV is
[available here](https://github.com/kelvins/US-Cities-Database/blob/main/csv/us_cities.csv).
It contains `ID`, `STATE_CODE`, `STATE_NAME`, `CITY`, `COUNTY`, `LATITUDE`,
and `LONGITUDE`. The repository publishes it under the MIT license:
Copyright (c) 2017 Kelvin S. do Prado. The full notice is retained in
[`data/US-Cities-Database.LICENSE`](data/US-Cities-Database.LICENSE) and must
accompany copies or substantial portions of the dataset. City records also
retain source attribution.

### City import result

Initial import with no previously resolved cities (individual unresolved-city
messages omitted):

```text
Import complete: resolved 3724, preserved 0, ambiguous 78, not found 6.
```

Successful matches are saved. The command then exits with a nonzero status to
flag the ambiguous and missing cities for review.

### City import behavior

`import_city_locations` makes no network requests. Import fuel prices first to
create the city records and station links. The offline import:

- Matches normalized `CITY` and `STATE_CODE`.
- Resolves only unique dataset IDs; does not guess among same-name cities in
  different counties or apply fuzzy spelling/abbreviation corrections.
- Processes existing US station cities only; Canadian cities remain unchanged.
- Preserves all already-resolved records.
- Saves dataset `ID`, provider URL, attribution, and import time.
- Reports missing and ambiguous matches individually with a nonzero exit status,
  while still committing successfully matched cities. These unmatched records
  may be retried after correcting the source or using a reviewed fallback.
- Rejects malformed/invalid source files before making any database changes.

Use `--dry-run` to inspect coverage without saving. Repeated imports do not
overwrite resolved cities by default. To switch an existing database from the
previous SimpleMaps source, explicitly re-evaluate all US station cities:

```powershell
python manage.py import_city_locations "data\us_cities.csv" --replace-resolved --dry-run
python manage.py import_city_locations "data\us_cities.csv" --replace-resolved
```

`--replace-resolved` replaces coordinates and provenance on unique matches.
It also clears previously resolved coordinates when the replacement file has
no unique match, marking the city not found or ambiguous. Review the dry-run
output first. Canadian records and station-specific coordinates are untouched.
City coordinates from different sources can differ; none of them represents
a verified truck-stop entrance, and valid numeric ranges do not prove accuracy.

The supplied local dataset was checked against the fuel CSV: exact unique
matches cover 3,724 of 3,808 US cities and 6,286 of 6,626 US stations (94.87%).
78 city/state keys are ambiguous (328 stations) and 6 have no exact match
(12 stations). Missing cities are Port Wentworth, GA; Evergreen, AL; Henrico,
VA; Elizabethport, NJ; Brookpark, OH; and University Park, IL. These figures
describe source coverage before any fallback enrichment, not guaranteed route
coverage. Dataset updates may change these counts.

No external geocoding service, API key, identifying User-Agent, or network
access is required for city enrichment.

## Validation

```powershell
python manage.py test truckstops
python manage.py makemigrations --check --dry-run
```
