# SmartFuel

See [assignment requirements](REQUIREMENTS.md) for the exercise scope,
delivery requirements, and implementation assumptions.

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

## Truck-stop catalog API

Read-only endpoints expose all imported stations, including Canadian stations
and stations with unresolved or absent city locations:

```http
GET /api/truckstops/
GET /api/truckstops/?country=US&state=OK&city=big
GET /api/truckstops/<id>/
```

List responses contain `count`, `next`, `previous`, and `results`, ordered by
OPIS ID with 50 records per page (`?page=2`). Optional `country` (`US` or `CA`)
and `state` filters match codes exactly, ignoring case; `city` is a
case-insensitive substring search. Filters can be combined. Invalid codes
or country/state combinations return JSON 400.

Detail URLs use the database `id`, not `opis_id`. Each station includes both
identifiers, name, address, city/state/country, rack ID, retail price (an
eight-decimal string), station-specific latitude/longitude, update time, and
nested `city_location`. The latter exposes city-level coordinates, precision,
match status, provider, source ID, and attribution. An unlinked station has
`city_location: null`; an unresolved city has a non-resolved status and null
coordinates. City coordinates are not verified station coordinates.

Missing stations return JSON 404. Write methods return JSON 405. Catalog
visibility does not imply route eligibility: fuel planning still excludes
Canadian and unresolved locations. Canadian price units/currency are not
inferred from the catalog. No external API calls are made for these endpoints.
These endpoints currently use the same unauthenticated access policy as the
other assessment API endpoints.

## Routing API

Routing uses [openrouteservice](https://openrouteservice.org/), which offers a
free tier requiring an API key.

To obtain `ORS_API_KEY`:

1. Register for a free account in the
   [openrouteservice dashboard](https://playground.heigit.org/#/signup).
2. Sign in and create an API key using the free plan in the dashboard.
3. Copy that key and set it as `ORS_API_KEY` before starting Django, as shown
   below. This is the routing provider's key, not an authentication token for
   this application's endpoints.

In PowerShell:

```powershell
$env:ORS_API_KEY = "YOUR_API_KEY"
python manage.py createcachetable
python manage.py runserver
```

In Git Bash, use `export ORS_API_KEY='YOUR_API_KEY'`. If using a local `.env`,
export its assignments (`set -a; source .env; set +a`) before starting Django;
the application does not automatically load it. Never commit API keys.
The key is sent to openrouteservice only, not returned to the frontend.

### Select cities

```http
GET /api/cities/?search=big&state=OK
```

The response contains `count`, `next`, `previous`, and `results`. Each result
has `id`, `city`, `state`, `country`, and `precision`. Results are sorted by
city/state/ID, with 50 per page (`?page=2`). Search is case-insensitive, and the
optional `state` filter accepts US state codes. Only resolved US city records
are listed. The current catalog covers fuel-stop cities, not every US city.
Fetch IDs from this endpoint rather than hardcoding them across database resets.

Retrieve one selectable city with `GET /api/cities/<id>/`. It returns the same
fields as a list entry. Unknown IDs, unresolved cities, and non-US cities return
HTTP 404 with a JSON `detail` error.

API responses use JSON rendering, not DRF's HTML browsable API. Unknown paths
under `/api/` also return `{"detail": "Not found."}` with HTTP 404, even with
`DEBUG=True`. Use the documented trailing slashes; unmatched API paths are not
redirected. Django admin and non-API error pages retain their normal behavior.
For example, `POST /api/routes` (missing trailing slash) returns JSON 404,
not a CSRF error. The API's not-found fallback performs no state changes and
does not require a CSRF token. Django admin retains CSRF protection; DRF's
session authentication also requires CSRF tokens for authenticated unsafe
requests. For anonymous Postman testing, do not send an admin session cookie.

### Request a route

```http
POST /api/routes/
Content-Type: application/json

{
  "start_city_id": 123,
  "finish_city_id": 456
}
```

Replace the example IDs with distinct cities from the list endpoint. The
response includes `start`, `finish`, `route` (a GeoJSON Feature with a complete
LineString), provider/location attribution, and `cached`. Route properties
contain `distance_meters`, `distance_miles`, and `duration_seconds`.
Geometry uses `[longitude, latitude]` order. The response also includes
`fuel_plan`, described below. A rendered map is not implemented yet.

### Fuel purchases and cost

`fuel_plan` contains:

- `fuel_stops`: purchases in travel order, including the origin; station
  identifiers, approximate city coordinates/attribution, distance along the
  route, straight-line corridor offset, price, gallons purchased, fuel on
  arrival/departure, and purchase cost. A station passed without buying is
  not listed as a stop.
- `legs`: travel between purchases, followed by the destination leg, with
  distance, gallons consumed, and departure/arrival fuel.
- `total_gallons_purchased`, `total_fuel_cost`, and `currency` (`USD`).
  Decimal fuel/money values are strings. Purchase costs are rounded to cents;
  the total is the sum of those displayed purchase costs.
- Vehicle parameters, corridor width, `estimated: true`, and a limitation warning.

The vehicle starts empty, buys the cost-optimal quantity at the cheapest listed
origin-city station, and has a 50-gallon tank at 10 MPG. No destination reserve
is required. A short trip buys only the fuel needed; an initially full tank is
not assumed or treated as free. A trip longer than 500 miles is allowed, but
every modeled leg must be reachable with the remaining fuel.
The objective minimizes unrounded fuel expenditure, not stop count or travel
time. There is no stop penalty or minimum purchase, so small purchases and
multiple closely spaced stops can be selected. Individual cost rounding can
create cent-level differences from the unrounded objective.

Only resolved US station cities within five straight-line miles of the route
are candidates. Their route progress is estimated from segment projections,
scaled to the provider's total driving distance. Actual station access and
detours are excluded. This gives minimum-cost purchases for the approximate
fixed-route model, not a globally cheapest itinerary or verified real-world
station reachability. All assumptions are in [requirements](REQUIREMENTS.md).

If no priced origin station or feasible station chain exists, the endpoint
returns HTTP 422 with a `detail` message; it does not fabricate stops or prices.
USD per US gallon is assumed for US price records.
On a geometry cache hit, fuel recommendations still use current database prices.

To check a plan in Postman:

1. Choose two resolved US city IDs and POST them to `/api/routes/`.
2. Verify the origin purchase is included and each listed purchase is positive.
3. Check each leg is at most 500 miles, fuel never goes below zero or above
   50 gallons, and consumption equals distance divided by 10 (allowing display
   rounding: miles show six decimal places and fuel shows eight).
4. Sum purchase costs; they must equal `total_fuel_cost`. With zero initial
   fuel/reserve, total gallons purchased equal total route miles divided by 10,
   subject to display rounding.
5. Repeat the request: `cached` should be true while prices are still recomputed.
6. A route without enough candidate coverage returns 422, not 429.

Each cache miss makes one driving-car GeoJSON directions request, with no
endpoint-geocoding requests or automatic retries. Border crossings and ferries
are prohibited in that request. See [requirements](REQUIREMENTS.md) for all
behavioral assumptions, including vehicle-profile and location limitations.
The hosted service has
[distance and other restrictions](https://giscience.github.io/openrouteservice/hosted-api-restrictions);
its documented standard driving-route maximum is 6,000 km. Quotas and service
availability are provider-dependent; a free tier is not an unlimited service.

Routes are cached for one hour in Django's database cache. Run
`python manage.py createcachetable` once after database setup (and after a
database reset). Cached routes are shared between workers and survive process
restarts. Coordinate changes produce different cache keys; fuel prices do not
invalidate geometry. Concurrent identical misses are not coalesced.
Do not delete the cache table while serving requests.

Errors are explicit: invalid/unavailable city IDs or identical endpoints return
400; unsupported/no-route provider results or infeasible fuel plans return 422; missing keys,
provider access denial, or quota exhaustion return 503; upstream failures or
malformed responses return 502; timeouts return 504. Failed responses are never
cached. Provider request timeout is 20 seconds. Provider failures are logged
without exposing API keys or raw upstream response bodies.

Automated routing tests mock the provider; they do not consume API quota.
Use Postman with a real key for live verification. Token/role authentication is
not enabled yet; do not expose this assessment endpoint publicly without access
controls and quota protection.

## Production recommendations

The current SQLite database and database-backed route cache are sufficient
for this assessment. For a production deployment:

- **Database:** prefer PostgreSQL for concurrent application workers, stronger
  operational tooling, and managed backups. Consider PostGIS if station
  proximity/corridor searches need spatial indexes; it is not required merely
  to store city coordinates. Moving to PostgreSQL does not reduce external
  routing-provider latency.
- **Cache:** use shared Redis through Django's cache framework for route
  caching, reducing database load and cache-access latency. Keep the route TTL
  and coordinate/options-based keys. Configure memory limits and eviction;
  cached routes are disposable and must be safe to recompute. Redis persistence
  is optional, unlike durable station/city data in PostgreSQL.
- **Provider quota:** add request coalescing for identical cache misses,
  application-level throttling, and monitoring of cache hit rate and upstream
  errors. A faster cache alone does not prevent concurrent misses from
  consuming multiple routing requests.

These are recommendations, not configured production infrastructure.

## Validation

```powershell
python manage.py test truckstops routing
python manage.py makemigrations --check --dry-run
```
