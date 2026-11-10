import copy
import json
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.core.cache import caches
from django.core.management import call_command
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from truckstops.models import CityLocation, TruckStop

from .services import ATTRIBUTION, ORS_URL

TEST_CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    "routes": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "routing-tests"},
}
PROVIDER_RESPONSE = {
    "type": "FeatureCollection",
    "features": [{
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [[-95.274736, 36.607931], [-94.5, 37.0], [-90.491704, 43.994833]],
        },
        "properties": {"summary": {"distance": 1609344, "duration": 54000}},
    }],
}


@override_settings(CACHES=TEST_CACHES, ORS_API_KEY="test-key", ROUTE_CACHE_TIMEOUT=3600)
class RoutingTests(TestCase):
    def setUp(self) -> None:
        caches["routes"].clear()
        self.client = APIClient()
        self.start = self.city("Big Cabin", "OK", "36.607931", "-95.274736")
        self.finish = self.city("Tomah", "WI", "43.994833", "-90.491704")
        self.payload = {"start_city_id": self.start.pk, "finish_city_id": self.finish.pk}
        self.urlopen_patcher = patch("routing.services.urlopen")
        self.urlopen = self.urlopen_patcher.start()
        self.addCleanup(self.urlopen_patcher.stop)
        self.respond(PROVIDER_RESPONSE)

    @staticmethod
    def city(name, state, latitude, longitude, country="US", status="resolved"):
        return CityLocation.objects.create(
            city=name, state=state, country=country, status=status,
            latitude=latitude, longitude=longitude,
            attribution="US Cities Database, MIT License",
        )

    def respond(self, data) -> None:
        self.urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(data).encode()

    def post_route(self, payload=None):
        return self.client.post("/api/routes/", self.payload if payload is None else payload, format="json")

    def test_city_list_filters_orders_and_excludes_unavailable_locations(self) -> None:
        self.city("Unresolved", "OK", None, None, status="pending")
        self.city("Toronto", "ON", 43, -79, country="CA")
        self.city("Unknown region", "ZZ", 36, -95)
        response = self.client.get("/api/cities/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)
        self.assertIsNone(response.data["next"])
        self.assertIsNone(response.data["previous"])
        self.assertEqual(
            response.data["results"][0],
            {"id": self.start.pk, "city": "big cabin", "state": "OK", "country": "US", "precision": "city"},
        )
        response = self.client.get("/api/cities/", {"search": "BIG   cabin", "state": "ok"})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], self.start.pk)
        self.urlopen.assert_not_called()

    def test_city_pagination_has_bounded_results_and_stable_order(self) -> None:
        for i in range(52):
            self.city(f"City {i:02}", "OK", 36, -95)
        response = self.client.get("/api/cities/")
        self.assertEqual(response.data["count"], 54)
        self.assertEqual(len(response.data["results"]), 50)
        self.assertIsNotNone(response.data["next"])
        second = self.client.get("/api/cities/?page=2")
        self.assertEqual(len(second.data["results"]), 4)
        first_ids = {row["id"] for row in response.data["results"]}
        self.assertFalse(first_ids.intersection(row["id"] for row in second.data["results"]))

    def test_city_filters_validate_and_missing_page_returns_404(self) -> None:
        self.assertEqual(self.client.get("/api/cities/?state=ON").status_code, 400)
        self.assertEqual(self.client.get("/api/cities/", {"search": "x" * 101}).status_code, 400)
        self.assertEqual(self.client.get("/api/cities/?page=999").status_code, 404)

    def test_city_detail_returns_same_fields_as_list(self) -> None:
        response = self.client.get(f"/api/cities/{self.start.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        listed = self.client.get("/api/cities/").data["results"][0]
        self.assertEqual(response.data, listed)
        self.urlopen.assert_not_called()

    def test_city_detail_hides_unresolved_and_non_us_cities(self) -> None:
        pending = self.city("Pending", "OK", None, None, status="pending")
        canadian = self.city("Toronto", "ON", 43, -79, country="CA")
        for city_id in (pending.pk, canadian.pk, 999999):
            with self.subTest(city_id=city_id):
                response = self.client.get(f"/api/cities/{city_id}/")
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response["Content-Type"], "application/json")
                self.assertIn("detail", response.json())
        self.urlopen.assert_not_called()

    def test_api_404_is_json_regardless_of_debug_or_browser_accept_header(self) -> None:
        for debug in (True, False):
            with override_settings(DEBUG=debug):
                for url in (
                    "/api/unknown/", "/api/cities/not-an-id/",
                    "/api/cities/999999/", "/api/cities/1/unknown/",
                ):
                    with self.subTest(debug=debug, url=url):
                        response = self.client.get(url, HTTP_ACCEPT="text/html,*/*")
                        self.assertEqual(response.status_code, 404)
                        self.assertEqual(response["Content-Type"], "application/json")
                        self.assertIn("detail", response.json())
                        self.assertNotContains(response, "URLconf", status_code=404)
                response = self.client.post("/api/unknown/", {}, format="json")
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json(), {"detail": "Not found."})

    def test_non_api_404_and_admin_are_not_replaced(self) -> None:
        with override_settings(DEBUG=True):
            response = self.client.get("/unknown/")
            self.assertEqual(response.status_code, 404)
            self.assertTrue(response["Content-Type"].startswith("text/html"))
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_city_detail_disallows_writes_with_json_error(self) -> None:
        response = self.client.post(f"/api/cities/{self.start.pk}/", {}, format="json")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn("detail", response.json())

    def test_unmatched_api_posts_return_json_with_csrf_checks_enabled(self) -> None:
        client = Client(enforce_csrf_checks=True)
        for debug in (True, False):
            with override_settings(DEBUG=debug):
                for url in ("/api/routes", "/api/route/", "/api/unknown/"):
                    with self.subTest(debug=debug, url=url):
                        response = client.post(url, data="{}", content_type="application/json")
                        self.assertEqual(response.status_code, 404)
                        self.assertEqual(response["Content-Type"], "application/json")
                        self.assertEqual(response.json(), {"detail": "Not found."})
        self.urlopen.assert_not_called()

    def test_anonymous_route_post_needs_no_csrf_cookie_but_admin_still_does(self) -> None:
        client = Client(enforce_csrf_checks=True)
        response = client.post("/api/routes/", data=json.dumps(self.payload), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(client.post("/admin/login/", data={}).status_code, 403)

    def test_route_calls_provider_once_and_returns_complete_geojson(self) -> None:
        response = self.post_route()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["cached"])
        self.assertEqual(response.data["start"]["id"], self.start.pk)
        self.assertEqual(response.data["finish"]["id"], self.finish.pk)
        self.assertEqual(response.data["provider"], "openrouteservice")
        self.assertEqual(response.data["attribution"], ATTRIBUTION)
        self.assertEqual(response.data["location_attribution"], [self.start.attribution])
        route = response.data["route"]
        self.assertEqual(route["type"], "Feature")
        self.assertEqual(route["geometry"], PROVIDER_RESPONSE["features"][0]["geometry"])
        self.assertEqual(route["properties"], {
            "distance_meters": 1609344.0, "distance_miles": 1000.0, "duration_seconds": 54000.0,
        })
        self.urlopen.assert_called_once()
        request = self.urlopen.call_args.args[0]
        self.assertEqual(request.full_url, ORS_URL)
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.get_header("Authorization"), "test-key")
        self.assertEqual(json.loads(request.data), {
            "coordinates": [[-95.274736, 36.607931], [-90.491704, 43.994833]],
            "instructions": False,
            "options": {"avoid_borders": "all", "avoid_features": ["ferries"]},
        })
        self.assertEqual(self.urlopen.call_args.kwargs["timeout"], 20)

    def test_identical_request_uses_cache_and_cache_survives_client_changes(self) -> None:
        self.post_route()
        self.client = APIClient()
        response = self.post_route()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["cached"])
        self.assertEqual(self.urlopen.call_count, 1)

    def test_direction_and_coordinate_changes_invalidate_cache(self) -> None:
        self.post_route()
        reverse = self.post_route({"start_city_id": self.finish.pk, "finish_city_id": self.start.pk})
        self.assertFalse(reverse.data["cached"])
        CityLocation.objects.filter(pk=self.finish.pk).update(latitude=44)
        response = self.post_route()
        self.assertFalse(response.data["cached"])
        self.assertEqual(self.urlopen.call_count, 3)

    def test_expired_cache_calls_provider_again(self) -> None:
        with override_settings(ROUTE_CACHE_TIMEOUT=0):
            self.post_route()
        self.post_route()
        self.assertEqual(self.urlopen.call_count, 2)

    def test_price_updates_do_not_change_route_cache_key(self) -> None:
        station = TruckStop.objects.create(
            opis_id=7, name="Test stop", address="I-44", city=self.start.city,
            state=self.start.state, country="US", rack_id=307,
            retail_price=3, city_location=self.start,
        )
        self.post_route()
        station.retail_price = 4
        station.save()
        response = self.post_route()
        self.assertTrue(response.data["cached"])
        self.assertEqual(self.urlopen.call_count, 1)

    def test_invalid_ids_identical_endpoints_and_non_us_cities_make_no_requests(self) -> None:
        canada = self.city("Toronto", "ON", 43, -79, country="CA")
        pending = self.city("Pending", "OK", None, None, status="pending")
        same_coords = self.city("Same coordinates", "OK", self.start.latitude, self.start.longitude)
        invalid = [
            {}, {"start_city_id": self.start.pk},
            {**self.payload, "start_city_id": 999999},
            {**self.payload, "start_city_id": canada.pk},
            {**self.payload, "start_city_id": pending.pk},
            {**self.payload, "start_city_id": self.finish.pk},
            {**self.payload, "finish_city_id": same_coords.pk},
            {**self.payload, "start_city_id": "not-an-id"},
            {**self.payload, "start_city_id": True},
            {**self.payload, "start_city_id": None},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(self.post_route(payload).status_code, 400)
        self.urlopen.assert_not_called()

    def test_missing_api_key_is_explicit_but_existing_cache_can_be_used(self) -> None:
        with override_settings(ORS_API_KEY=""):
            self.assertEqual(self.post_route().status_code, 503)
        self.urlopen.assert_not_called()
        self.post_route()
        with override_settings(ORS_API_KEY=""):
            self.assertEqual(self.post_route().status_code, 200)
        self.assertEqual(self.urlopen.call_count, 1)

    def test_transport_failures_are_explicit_and_never_cached(self) -> None:
        for error, expected in (
            (TimeoutError(), 504), (URLError(TimeoutError()), 504),
            (URLError("unavailable"), 502), (OSError("unavailable"), 502),
        ):
            with self.subTest(error=error):
                self.urlopen.side_effect = error
                self.assertEqual(self.post_route().status_code, expected)
        self.urlopen.side_effect = None
        response = self.post_route()
        self.assertFalse(response.data["cached"])
        self.assertEqual(self.urlopen.call_count, 5)

    def test_provider_http_errors_are_mapped_without_exposing_provider_details(self) -> None:
        for http_code, provider_code, expected in (
            (401, None, 503), (403, None, 503), (429, None, 503),
            (500, None, 502), (400, 2004, 422), (404, 2010, 422),
            (404, 2009, 422), (400, 2000, 502),
        ):
            with self.subTest(http_code=http_code, provider_code=provider_code):
                body = json.dumps({"error": {"code": provider_code, "message": "secret provider detail"}}).encode()
                self.urlopen.side_effect = HTTPError(ORS_URL, http_code, "error", {}, BytesIO(body))
                response = self.post_route()
                self.assertEqual(response.status_code, expected)
                self.assertNotIn("secret", str(response.data))
        self.assertEqual(self.urlopen.call_count, 8)

    def test_invalid_json_is_rejected(self) -> None:
        self.urlopen.return_value.__enter__.return_value.read.return_value = b"not-json"
        self.assertEqual(self.post_route().status_code, 502)
        self.respond(PROVIDER_RESPONSE)
        self.assertFalse(self.post_route().data["cached"])

    def test_malformed_provider_data_is_rejected_without_caching(self) -> None:
        invalid = [None, [], {}, {"type": "FeatureCollection", "features": []}]
        for field, value in (("distance", -1), ("distance", True), ("distance", float("nan")), ("duration", -1)):
            data = copy.deepcopy(PROVIDER_RESPONSE)
            data["features"][0]["properties"]["summary"][field] = value
            invalid.append(data)
        for geometry in (
            {"type": "Point", "coordinates": [-95, 36]},
            {"type": "LineString", "coordinates": []},
            {"type": "LineString", "coordinates": [[-181, 36], [-90, 43]]},
            {"type": "LineString", "coordinates": [[-95, 91], [-90, 43]]},
            {"type": "LineString", "coordinates": [[-95, 36, 0], [-90, 43, 0]]},
        ):
            data = copy.deepcopy(PROVIDER_RESPONSE)
            data["features"][0]["geometry"] = geometry
            invalid.append(data)
        for data in invalid:
            with self.subTest(data=data):
                self.respond(data)
                self.assertEqual(self.post_route().status_code, 502)
        self.respond(PROVIDER_RESPONSE)
        self.assertFalse(self.post_route().data["cached"])


@override_settings(
    ORS_API_KEY="test-key",
    CACHES={
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
        "routes": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "route_cache"},
    },
)
class DatabaseRouteCacheTests(TransactionTestCase):
    def test_route_cache_is_stored_in_database_and_reused(self) -> None:
        call_command("createcachetable", verbosity=0)
        caches["routes"].clear()
        start = RoutingTests.city("Big Cabin", "OK", "36.607931", "-95.274736")
        finish = RoutingTests.city("Tomah", "WI", "43.994833", "-90.491704")
        payload = {"start_city_id": start.pk, "finish_city_id": finish.pk}
        with patch("routing.services.urlopen") as upstream:
            upstream.return_value.__enter__.return_value.read.return_value = json.dumps(PROVIDER_RESPONSE).encode()
            first = APIClient().post("/api/routes/", payload, format="json")
            self.assertEqual(first.status_code, 200)
            self.assertFalse(first.data["cached"])
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM route_cache")
                self.assertEqual(cursor.fetchone()[0], 1)
            caches.close_all()
            second = APIClient().post("/api/routes/", payload, format="json")
            self.assertEqual(second.status_code, 200)
            self.assertTrue(second.data["cached"])
            self.assertEqual(second.data["route"], first.data["route"])
            upstream.assert_called_once()
