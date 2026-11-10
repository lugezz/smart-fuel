import hashlib
import json
import logging
import math
from dataclasses import dataclass
from typing import TypedDict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import caches
from rest_framework.exceptions import APIException

from truckstops.models import CityLocation

logger = logging.getLogger(__name__)
ORS_URL = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
ATTRIBUTION = "openrouteservice | OpenStreetMap contributors"


class Geometry(TypedDict):
    type: str
    coordinates: list[list[float]]


class RouteProperties(TypedDict):
    distance_meters: float
    distance_miles: float
    duration_seconds: float


class RouteFeature(TypedDict):
    type: str
    geometry: Geometry
    properties: RouteProperties


@dataclass(frozen=True)
class RouteResult:
    feature: RouteFeature
    cached: bool


class RoutingError(APIException):
    def __init__(self, detail: str, *, status_code: int, code: str) -> None:
        self.status_code = status_code
        super().__init__(detail, code=code)


def get_route(start: CityLocation, finish: CityLocation) -> RouteResult:
    payload = {
        "coordinates": [
            [float(start.longitude), float(start.latitude)],
            [float(finish.longitude), float(finish.latitude)],
        ],
        "instructions": False,
        "options": {"avoid_borders": "all", "avoid_features": ["ferries"]},
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    key = "route:v1:" + hashlib.sha256(ORS_URL.encode() + body).hexdigest()
    cache = caches["routes"]
    cached = cache.get(key)
    if cached is not None:
        return RouteResult(feature=cached, cached=True)
    if not settings.ORS_API_KEY.strip():
        logger.error("Routing unavailable: ORS_API_KEY is not configured.")
        raise RoutingError("Routing provider is not configured.", status_code=503, code="routing_not_configured")
    request = Request(
        ORS_URL, data=body, method="POST",
        headers={
            "Authorization": settings.ORS_API_KEY,
            "Content-Type": "application/json",
            "Accept": "application/geo+json",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            data = json.loads(response.read())
    except HTTPError as exc:
        raise provider_http_error(exc) from None
    except (TimeoutError, URLError, OSError) as exc:
        logger.warning("Routing provider transport failure: %s", type(exc).__name__)
        timeout = isinstance(exc, TimeoutError) or (
            isinstance(exc, URLError) and isinstance(exc.reason, TimeoutError)
        )
        raise RoutingError(
            "Routing provider timed out." if timeout else "Routing provider is unavailable.",
            status_code=504 if timeout else 502, code="routing_transport_error",
        ) from None
    except (ValueError, UnicodeError):
        logger.warning("Routing provider returned invalid JSON.")
        raise RoutingError("Invalid routing provider response.", status_code=502, code="invalid_route_response") from None
    try:
        feature = parse_route(data)
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        logger.warning("Routing provider response failed route validation.")
        raise RoutingError("Invalid routing provider response.", status_code=502, code="invalid_route_response") from None
    cache.set(key, feature, timeout=settings.ROUTE_CACHE_TIMEOUT)
    return RouteResult(feature=feature, cached=False)


def provider_http_error(error: HTTPError) -> RoutingError:
    logger.warning("Routing provider returned HTTP %s.", error.code)
    if error.code in (401, 403, 429):
        return RoutingError(
            "Routing provider access or quota is unavailable.", status_code=503, code="routing_unavailable",
        )
    if error.code in (400, 404):
        try:
            data = json.loads(error.read(65536))
            code = data["error"]["code"]
        except (ValueError, UnicodeError, KeyError, TypeError):
            code = None
        if code in (2004, 2009, 2010):
            return RoutingError(
                "No supported driving route found: endpoints may be disconnected, unsnappable, or exceed provider limits.",
                status_code=422, code="route_unavailable",
            )
    return RoutingError("Routing provider failed.", status_code=502, code="routing_provider_error")


def finite_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Expected finite number.")
    return float(value)


def parse_route(data: dict) -> RouteFeature:
    if data["type"] != "FeatureCollection" or len(data["features"]) != 1:
        raise ValueError("Expected one route feature.")
    feature = data["features"][0]
    geometry = feature["geometry"]
    if feature["type"] != "Feature" or geometry["type"] != "LineString":
        raise ValueError("Expected route LineString.")
    coordinates = geometry["coordinates"]
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        raise ValueError("Route needs at least two coordinates.")
    points = []
    for point in coordinates:
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError("Expected longitude/latitude pairs.")
        longitude, latitude = (finite_number(value) for value in point)
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            raise ValueError("Coordinates out of range.")
        points.append([longitude, latitude])
    summary = feature["properties"]["summary"]
    distance = finite_number(summary["distance"])
    duration = finite_number(summary["duration"])
    if distance <= 0 or duration < 0:
        raise ValueError("Invalid route distance/duration.")
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": points},
        "properties": {
            "distance_meters": distance,
            "distance_miles": distance / 1609.344,
            "duration_seconds": duration,
        },
    }
