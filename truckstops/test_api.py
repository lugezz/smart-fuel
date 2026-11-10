from django.test import TestCase
from rest_framework.test import APIClient

from .models import CityLocation, TruckStop


class TruckStopAPITests(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()
        self.resolved = CityLocation.objects.create(
            city="Big Cabin", state="OK", country="US", status="resolved",
            latitude="36.607931", longitude="-95.274736",
            provider="https://github.com/kelvins/US-Cities-Database",
            attribution="US Cities Database, MIT License",
        )
        self.pending = CityLocation.objects.create(city="Toronto", state="ON", country="CA")
        self.us = self.station(7, "Big Cabin", "OK", "US", self.resolved)
        self.canadian = self.station(8, "Toronto", "ON", "CA", self.pending)
        self.unlinked = self.station(9, "Missing", "TX", "US", None)

    def station(self, opis_id, city, state, country, location):
        return TruckStop.objects.create(
            opis_id=opis_id, name=f"Stop {opis_id}", address="Highway exit",
            city=city, state=state, country=country, rack_id=307,
            retail_price="3.00733333", city_location=location,
        )

    def test_list_includes_all_countries_and_location_statuses(self) -> None:
        response = self.client.get("/api/truckstops/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.data["count"], 3)
        self.assertIsNone(response.data["next"])
        self.assertIsNone(response.data["previous"])
        rows = response.data["results"]
        self.assertEqual([row["opis_id"] for row in rows], [7, 8, 9])
        self.assertEqual(rows[0]["retail_price"], "3.00733333")
        self.assertIsNone(rows[0]["latitude"])
        self.assertEqual(rows[0]["city_location"]["latitude"], "36.607931")
        self.assertEqual(rows[0]["city_location"]["precision"], "city")
        self.assertEqual(rows[0]["city_location"]["attribution"], self.resolved.attribution)
        self.assertEqual(rows[1]["city_location"]["status"], "pending")
        self.assertIsNone(rows[1]["city_location"]["latitude"])
        self.assertIsNone(rows[2]["city_location"])

    def test_filters_can_be_combined_and_normalize_input(self) -> None:
        cases = [
            ({"country": "ca"}, [8]),
            ({"state": "tx"}, [9]),
            ({"city": "BIG cabin"}, [7]),
            ({"country": "us", "state": "ok", "city": "CAB"}, [7]),
            ({"city": "unknown"}, []),
        ]
        for params, expected in cases:
            with self.subTest(params=params):
                response = self.client.get("/api/truckstops/", params)
                self.assertEqual(response.status_code, 200)
                self.assertEqual([row["opis_id"] for row in response.data["results"]], expected)

    def test_invalid_filters_return_json_400(self) -> None:
        for params in (
            {"country": "XX"}, {"state": "ZZ"}, {"country": "US", "state": "ON"},
            {"country": "CA", "state": "TX"}, {"city": "x" * 101},
        ):
            with self.subTest(params=params):
                response = self.client.get("/api/truckstops/", params)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response["Content-Type"], "application/json")

    def test_detail_by_database_id_matches_list(self) -> None:
        rows = self.client.get("/api/truckstops/").data["results"]
        for station, row in zip((self.us, self.canadian, self.unlinked), rows):
            response = self.client.get(f"/api/truckstops/{station.pk}/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, row)

    def test_missing_and_malformed_detail_ids_return_json_404(self) -> None:
        for path in ("/api/truckstops/999999/", "/api/truckstops/not-an-id/", "/api/truckstops/?page=99"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response["Content-Type"], "application/json")
                self.assertIn("detail", response.json())

    def test_endpoints_are_read_only(self) -> None:
        for method, url in (
            ("post", "/api/truckstops/"),
            ("put", f"/api/truckstops/{self.us.pk}/"),
            ("patch", f"/api/truckstops/{self.us.pk}/"),
            ("delete", f"/api/truckstops/{self.us.pk}/"),
        ):
            with self.subTest(method=method):
                response = getattr(self.client, method)(url, {}, format="json")
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(TruckStop.objects.count(), 3)

    def test_pagination_is_bounded_and_related_data_avoids_n_plus_one(self) -> None:
        for opis_id in range(10, 65):
            self.station(opis_id, "Big Cabin", "OK", "US", self.resolved)
        with self.assertNumQueries(2):
            first = self.client.get("/api/truckstops/")
        self.assertEqual(first.data["count"], 58)
        self.assertEqual(len(first.data["results"]), 50)
        self.assertIsNotNone(first.data["next"])
        with self.assertNumQueries(2):
            second = self.client.get("/api/truckstops/?page=2")
        self.assertEqual(len(second.data["results"]), 8)
        first_ids = {row["id"] for row in first.data["results"]}
        self.assertFalse(first_ids.intersection(row["id"] for row in second.data["results"]))
        with self.assertNumQueries(1):
            self.client.get(f"/api/truckstops/{self.us.pk}/")
