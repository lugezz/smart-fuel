import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from truckstops.models import US_STATES, CityLocation, TruckStop

from .services import RouteFeature, RoutingError

logger = logging.getLogger(__name__)
MPG = Decimal("10")
CAPACITY = Decimal("50")
MAX_RANGE = CAPACITY * MPG
CORRIDOR_MILES = 5
EARTH_RADIUS_MILES = 3958.7613
GRID_DEGREES = 0.25


@dataclass(frozen=True)
class FuelPoint:
    distance: Decimal
    price: Decimal
    station: TruckStop
    offset_miles: float = 0


@dataclass(frozen=True)
class Purchase:
    point: FuelPoint
    gallons: Decimal
    arrival_gallons: Decimal
    departure_gallons: Decimal


def infeasible(message: str) -> RoutingError:
    logger.info("Fuel plan unavailable: %s", message)
    return RoutingError(message, status_code=422, code="fuel_plan_unavailable")


def optimize_purchases(points: list[FuelPoint], total: Decimal) -> list[Purchase]:
    """Minimum-cost purchases on an ordered, fixed-distance path with no detour costs."""
    if not points or points[0].distance != 0:
        raise infeasible("No priced station is available in the origin city.")
    fuel = Decimal(0)
    previous_distance = Decimal(0)
    purchases = []
    for index, point in enumerate(points):
        fuel -= (point.distance - previous_distance) / MPG
        if fuel < 0:
            raise infeasible("Available station coverage leaves a gap beyond the 500-mile range.")
        target = min(CAPACITY, (total - point.distance) / MPG)
        # Buy only enough to reach the first cheaper station; otherwise fill
        # up to capacity or the destination. The destination has zero fuel price.
        for following in points[index + 1:]:
            gap = following.distance - point.distance
            if gap > MAX_RANGE:
                break
            if following.price < point.price:
                target = gap / MPG
                break
        amount = max(Decimal(0), target - fuel)
        if amount:
            purchases.append(Purchase(point, amount, fuel, fuel + amount))
            fuel += amount
        previous_distance = point.distance
    if fuel < (total - previous_distance) / MPG:
        raise infeasible("Available station coverage leaves a gap beyond the 500-mile range.")
    return purchases


def haversine(a: list[float], b: list[float]) -> float:
    lat1, lat2 = math.radians(a[1]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = math.radians(b[0] - a[0])
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(min(1, max(0, value))))


def segment_projection(a: list[float], b: list[float], longitude: float, latitude: float) -> tuple[float, float]:
    # Local tangent-plane approximation for corridor filtering, not road access.
    scale = math.cos(math.radians((a[1] + b[1]) / 2))
    bx, by = math.radians(b[0] - a[0]) * scale, math.radians(b[1] - a[1])
    px, py = math.radians(longitude - a[0]) * scale, math.radians(latitude - a[1])
    length_squared = bx * bx + by * by
    fraction = max(0, min(1, (px * bx + py * by) / length_squared)) if length_squared else 0
    offset = math.hypot(px - fraction * bx, py - fraction * by) * EARTH_RADIUS_MILES
    return offset, fraction


def project_cities(route: RouteFeature, cities: list[CityLocation]) -> dict[int, tuple[float, Decimal]]:
    grid = defaultdict(list)
    for city in cities:
        longitude, latitude = float(city.longitude), float(city.latitude)
        grid[(math.floor(longitude / GRID_DEGREES), math.floor(latitude / GRID_DEGREES))].append(
            (city.pk, longitude, latitude)
        )
    coordinates = route["geometry"]["coordinates"]
    lengths = [haversine(a, b) for a, b in zip(coordinates, coordinates[1:])]
    geometry_length = sum(lengths)
    if geometry_length <= 0:
        raise RoutingError("Route geometry has no measurable length.", status_code=502, code="invalid_route_geometry")
    total = Decimal(str(route["properties"]["distance_miles"]))
    nearest: dict[int, tuple[float, Decimal]] = {}
    cumulative = 0.0
    for a, b, length in zip(coordinates, coordinates[1:], lengths):
        latitude_pad = math.degrees(CORRIDOR_MILES / EARTH_RADIUS_MILES)
        max_latitude = max(abs(a[1]), abs(b[1])) + latitude_pad
        longitude_pad = latitude_pad / max(0.01, math.cos(math.radians(min(89, max_latitude))))
        x1 = math.floor((min(a[0], b[0]) - longitude_pad) / GRID_DEGREES)
        x2 = math.floor((max(a[0], b[0]) + longitude_pad) / GRID_DEGREES)
        y1 = math.floor((min(a[1], b[1]) - latitude_pad) / GRID_DEGREES)
        y2 = math.floor((max(a[1], b[1]) + latitude_pad) / GRID_DEGREES)
        for x in range(x1, x2 + 1):
            for y in range(y1, y2 + 1):
                for city_id, longitude, latitude in grid.get((x, y), ()):
                    offset, fraction = segment_projection(a, b, longitude, latitude)
                    if offset <= CORRIDOR_MILES and (city_id not in nearest or offset < nearest[city_id][0]):
                        distance = total * Decimal(str((cumulative + fraction * length) / geometry_length))
                        nearest[city_id] = (
                            offset, min(total, max(Decimal(0), distance.quantize(Decimal("0.00000001"))))
                        )
        cumulative += length
    return nearest


def fuel_points(route: RouteFeature, start: CityLocation, finish: CityLocation) -> list[FuelPoint]:
    stations = TruckStop.objects.filter(
        country="US", state__in=US_STATES,
        city_location__country="US", city_location__state__in=US_STATES,
        city_location__status=CityLocation.Status.RESOLVED,
    ).select_related("city_location").order_by("retail_price", "opis_id")
    cheapest: dict[int, TruckStop] = {}
    for station in stations:
        cheapest.setdefault(station.city_location_id, station)
    origin = cheapest.get(start.pk)
    if origin is None:
        raise infeasible("No priced station is available in the origin city.")
    cities = [station.city_location for key, station in cheapest.items() if key not in (start.pk, finish.pk)]
    projections = project_cities(route, cities)
    total = Decimal(str(route["properties"]["distance_miles"]))
    points = [FuelPoint(Decimal(0), origin.retail_price, origin)]
    for city_id, (offset, distance) in projections.items():
        if 0 < distance < total:
            station = cheapest[city_id]
            points.append(FuelPoint(distance, station.retail_price, station, offset))
    # Only the cheapest station at a shared route position is relevant.
    by_distance: dict[Decimal, FuelPoint] = {}
    for point in sorted(points, key=lambda p: (p.distance, p.price, p.station.opis_id)):
        by_distance.setdefault(point.distance, point)
    return list(by_distance.values())


def decimal_text(value: Decimal, places: str) -> str:
    return format(value.quantize(Decimal(places), rounding=ROUND_HALF_UP), "f")


def make_fuel_plan(route: RouteFeature, start: CityLocation, finish: CityLocation) -> dict:
    total = Decimal(str(route["properties"]["distance_miles"]))
    points = fuel_points(route, start, finish)
    purchases = optimize_purchases(points, total)
    stops, legs = [], []
    total_cost = Decimal(0)
    total_gallons = Decimal(0)
    previous_distance = Decimal(0)
    previous_departure = Decimal(0)
    for purchase in purchases:
        point, station = purchase.point, purchase.point.station
        leg_distance = point.distance - previous_distance
        if stops:
            legs.append(leg_data(leg_distance, previous_departure, purchase.arrival_gallons))
        cost = (purchase.gallons * point.price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_cost += cost
        total_gallons += purchase.gallons
        stops.append({
            "sequence": len(stops) + 1,
            "is_origin": point.distance == 0,
            "station": {
                "opis_id": station.opis_id,
                "name": station.name,
                "city": station.city, "state": station.state, "country": station.country,
                "city_location_id": station.city_location_id,
                "latitude": str(station.city_location.latitude),
                "longitude": str(station.city_location.longitude),
                "precision": "city", "attribution": station.city_location.attribution,
            },
            "distance_from_start_miles": decimal_text(point.distance, "0.000001"),
            "route_offset_miles": round(point.offset_miles, 6),
            "price_per_gallon": str(point.price),
            "gallons_purchased": decimal_text(purchase.gallons, "0.00000001"),
            "arrival_fuel_gallons": decimal_text(purchase.arrival_gallons, "0.00000001"),
            "departure_fuel_gallons": decimal_text(purchase.departure_gallons, "0.00000001"),
            "cost": str(cost),
        })
        previous_distance, previous_departure = point.distance, purchase.departure_gallons
    final_distance = total - previous_distance
    legs.append(leg_data(final_distance, previous_departure, previous_departure - final_distance / MPG))
    return {
        "estimated": True, "currency": "USD", "mpg": 10, "tank_capacity_gallons": 50, "max_range_miles": 500,
        "corridor_miles": CORRIDOR_MILES, "initial_fuel_gallons": 0, "destination_reserve_gallons": 0,
        "fuel_stops": stops, "legs": legs,
        "total_gallons_purchased": decimal_text(total_gallons, "0.00000001"),
        "total_fuel_cost": str(total_cost),
        "warning": (
            "City-level estimates only. Access/detour miles are excluded. Range feasibility and minimum cost "
            "apply only to the projected fixed-route model, not verified station driving distances."
        ),
    }


def leg_data(distance: Decimal, departure: Decimal, arrival: Decimal) -> dict:
    return {
        "distance_miles": decimal_text(distance, "0.000001"),
        "fuel_consumed_gallons": decimal_text(distance / MPG, "0.00000001"),
        "departure_fuel_gallons": decimal_text(departure, "0.00000001"),
        "arrival_fuel_gallons": decimal_text(arrival, "0.00000001"),
    }
