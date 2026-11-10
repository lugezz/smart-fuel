# Assignment requirements

## Functional requirements

- Build an API accepting start and finish locations, both within the USA.
- Return a map of the route with cost-effective fuel stops based on the
  supplied fuel-price dataset.
- Assume a maximum vehicle range of 500 miles; display multiple fuel stops
  when necessary.
- Return the total money spent on fuel, assuming fuel economy of 10 miles
  per gallon.
- Use the supplied `fuel-prices-for-be-assessment.csv` for fuel prices.
- Find and use a free API for mapping and routing.

## Technical and performance requirements

- Use the latest stable Django release.
- Return results quickly; faster responses are preferred.
- Minimize calls to the external map/routing API: one call is ideal;
  two or three calls are acceptable.

## Delivery requirements

- Submit the project within three days of receiving the exercise.
- Share the code on GitHub.
- Record a Loom video no longer than five minutes:
  - Demonstrate the working API using Postman or a similar API platform.
  - Give a brief overview of the code.

## Project assumptions and limitations

These are implementation decisions, not additional assignment requirements.

- Use offline city-level coordinates from
  [kelvins/US-Cities-Database](https://github.com/kelvins/US-Cities-Database).
  They approximate station locations and do not establish actual station
  access, detour distance, or real-world reachability.
- Exclude Canadian stations and unresolved US locations from route planning.
- Retain the lowest listed price per OPIS station ID; the CSV does not
  identify fuel types or price timestamps.
- Interpret the external-call target per route request. Offline CSV imports
  make no external calls.
- Optimize fuel purchases along a selected driving route; do not claim a
  globally cheapest route across the entire road network.
- Select endpoints by `CityLocation` database ID, obtained through the
  paginated/searchable city-list endpoint. Only resolved US cities are offered.
  The current catalog contains fuel-stop cities, not every US city; database
  IDs are not stable across database resets.
- Use openrouteservice's free-tier driving-car directions API with an API key.
  A cache miss makes one routing request and no endpoint-geocoding requests.
  Requests prohibit international border crossings and ferries; disconnected
  endpoints or routes outside the hosted provider's limits fail explicitly.
  This driving profile does not model truck height/weight/hazard restrictions.
- Cache route geometry, distance, and duration for one hour in Django's database
  cache, separately from fuel prices. Keys include ordered endpoint coordinates,
  provider URL, and routing options; changing coordinates invalidates reuse.
  Simultaneous identical misses may each make a provider request; cached
  responses survive application restarts and are shared between workers.
- Treat city representative coordinates as route endpoints, not exact addresses.
  The provider may snap them to nearby roads; snap/access distances are not
  fuel-station detour measurements.
- Include the initial fuel purchase at the selected origin in total money spent;
  do not treat an initially full tank as free fuel. This routing-only iteration
  does not yet calculate fuel purchases or return a rendered map.
- See the [README](README.md) for dataset provenance, import results, and
  detailed location limitations.

## Decisions to settle before fuel optimization

- Initial fuel quantity and the origin price when no listed station is available.
- Required fuel reserve at the destination.
- Allowed detour distance and how approximate city locations affect estimates.
- How to report insufficient station coverage or an infeasible fueling plan.
