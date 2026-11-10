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
- Expose all imported stations through the read-only truck-stop catalog,
  including Canadian and unresolved records. Catalog visibility is separate
  from route eligibility. Detail URLs use database IDs, not OPIS IDs; city
  coordinates and match status remain separate from station coordinates.
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
  start with an empty tank and buy the cost-optimal quantity at the cheapest
  listed station in the origin city. If no priced station exists there, return
  HTTP 422 rather than invent a price. US CSV prices are assumed to be USD per
  US gallon and comparable across listed stations.
- Tank capacity is 50 US gallons (500 miles at 10 MPG). There is no required
  destination reserve; purchases minimize fuel cost under the fixed-route model.
  At a station, buy enough to reach the first cheaper reachable station;
  otherwise fill to capacity or buy only enough to reach the destination.
- Do not charge a fixed cost/time penalty per stop or impose a minimum purchase.
  Small purchases and extra stops can therefore be optimal in this model.
  Minimize unrounded fuel expenditure, then round individual purchase costs
  for display; rounding can create cent-level ties or differences.
- Consider only station cities within five straight-line miles of the route,
  using their closest segment projection; ties use the first occurrence.
  Use the cheapest listed station per city, with OPIS ID breaking price ties.
  Exclude destination-city stations because fueling after arrival is unnecessary.
- Estimate station progress by cumulative haversine length along route geometry,
  scaled to the provider's total driving distance. Corridor offsets use local
  tangent-plane segment projection. This is not a road-distance matrix.
  No extra routing/geocoding calls are made for fuel selection.
- Ignore access and detour miles in the agreed approximate model. Responses
  explicitly label fuel plans as estimates; 500-mile feasibility and minimum
  cost are guaranteed only within this model, not for actual station access.
- Recompute fuel purchases from current database prices on every request,
  including route-cache hits. Do not cache price-dependent plans with geometry.
  Fuel amounts and money use decimal arithmetic; round each purchase cost to
  USD cents, then sum those rounded costs. Monetary fields are JSON strings.
- Return HTTP 422 if station gaps exceed the modeled 500-mile range or origin
  pricing is unavailable. Trips longer than 500 miles are valid when a feasible
  plan exists; HTTP 429 means rate limiting, not a long trip.
- The API returns route GeoJSON plus purchases and leg fuel balances. A rendered
  map is still outstanding; GeoJSON alone is not the final map deliverable.
- See the [README](README.md) for dataset provenance, import results, and
  detailed location limitations.
