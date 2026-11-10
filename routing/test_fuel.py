import random
from decimal import Decimal

from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from truckstops.models import CityLocation, TruckStop

from .fuel import FuelPoint, make_fuel_plan, optimize_purchases, project_cities
from .services import RoutingError


class PurchaseOptimizerTests(SimpleTestCase):
    def point(self, distance, price) -> FuelPoint:
        station = TruckStop(opis_id=distance + 1, retail_price=price)
        return FuelPoint(Decimal(distance), Decimal(price), station)

    def test_short_trip_buys_only_required_fuel_at_origin(self) -> None:
        purchases = optimize_purchases([self.point(0, 3)], Decimal(100))
        self.assertEqual(len(purchases), 1)
        self.assertEqual(purchases[0].gallons, 10)
        self.assertEqual(purchases[0].arrival_gallons, 0)

    def test_exact_500_mile_boundary(self) -> None:
        purchases = optimize_purchases([self.point(0, 3)], Decimal(500))
        self.assertEqual(purchases[0].gallons, 50)
        with self.assertRaises(RoutingError) as error:
            optimize_purchases([self.point(0, 3)], Decimal("500.000001"))
        self.assertEqual(error.exception.status_code, 422)

    def test_cheaper_ahead_buys_only_enough_to_reach_it(self) -> None:
        purchases = optimize_purchases([self.point(0, 4), self.point(300, 2)], Decimal(700))
        self.assertEqual([purchase.gallons for purchase in purchases], [30, 40])

    def test_expensive_ahead_uses_remaining_fuel_not_another_full_tank(self) -> None:
        purchases = optimize_purchases([self.point(0, 2), self.point(300, 4)], Decimal(700))
        self.assertEqual([purchase.gallons for purchase in purchases], [50, 20])
        self.assertEqual(purchases[1].arrival_gallons, 20)
        self.assertEqual(purchases[1].departure_gallons, 40)

    def test_skips_unnecessary_purchase_and_handles_multiple_stops(self) -> None:
        points = [self.point(0, 2), self.point(200, 5), self.point(400, 3), self.point(800, 4)]
        purchases = optimize_purchases(points, Decimal(1100))
        self.assertEqual([purchase.point.distance for purchase in purchases], [0, 400, 800])
        self.assertEqual(sum(p.gallons for p in purchases), Decimal(110))
        self.assertTrue(all(0 <= p.arrival_gallons <= p.departure_gallons <= 50 for p in purchases))

    def test_gap_between_stations_is_infeasible(self) -> None:
        with self.assertRaises(RoutingError):
            optimize_purchases([self.point(0, 3), self.point(501, 2)], Decimal(600))

    def test_matches_exhaustive_discrete_dynamic_program(self) -> None:
        generator = random.Random(42)
        for case in range(60):
            destination = generator.randint(55, 150)
            positions = [0, *sorted(generator.sample(range(1, destination), 8))]
            prices = [generator.randint(2, 6) for _ in positions]
            points = [self.point(position * 10, price) for position, price in zip(positions, prices)]
            # Independent integer-gallon DP enumerates all feasible purchase amounts.
            states = {0: 0}
            for index, position in enumerate(positions):
                next_position = positions[index + 1] if index + 1 < len(positions) else destination
                needed = next_position - position
                following_states = {}
                for arrival, spent in states.items():
                    for departure in range(max(arrival, needed), 51):
                        remaining = departure - needed
                        cost = spent + (departure - arrival) * prices[index]
                        following_states[remaining] = min(following_states.get(remaining, float("inf")), cost)
                states = following_states
            with self.subTest(case=case):
                if not states:
                    with self.assertRaises(RoutingError):
                        optimize_purchases(points, Decimal(destination * 10))
                else:
                    purchases = optimize_purchases(points, Decimal(destination * 10))
                    self.assertEqual(sum(p.gallons * p.point.price for p in purchases), min(states.values()))
                    self.assertEqual(sum(p.gallons for p in purchases), destination)


class FuelPlanTests(TestCase):
    def setUp(self) -> None:
        self.start = self.city("Start", -100)
        self.finish = self.city("Finish", -80)
        self.route = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[-100, 40], [-80, 40]]},
            "properties": {"distance_meters": 1609344, "distance_miles": 1000, "duration_seconds": 50000},
        }
        self.origin = self.station(self.start, 1, "4.12345678")

    def city(self, name, longitude, latitude=40, country="US", state="OK"):
        return CityLocation.objects.create(
            city=name, state=state, country=country, status="resolved",
            longitude=longitude, latitude=latitude, attribution="Test dataset",
        )

    def station(self, city, opis_id, price):
        return TruckStop.objects.create(
            opis_id=opis_id, name=f"Station {opis_id}", address="Road",
            city=city.city, state=city.state, country=city.country, rack_id=1,
            city_location=city, retail_price=Decimal(price),
        )

    def test_long_route_projects_cities_and_balances_fuel_and_cost(self) -> None:
        middle = self.city("Middle", -90)
        self.station(middle, 2, "3.12345678")
        self.station(middle, 3, "5.00")
        plan = make_fuel_plan(self.route, self.start, self.finish)
        self.assertEqual([s["station"]["opis_id"] for s in plan["fuel_stops"]], [1, 2])
        self.assertEqual([s["gallons_purchased"] for s in plan["fuel_stops"]], ["50.00000000", "50.00000000"])
        self.assertEqual(plan["total_gallons_purchased"], "100.00000000")
        self.assertEqual(
            Decimal(plan["total_fuel_cost"]), sum(Decimal(stop["cost"]) for stop in plan["fuel_stops"])
        )
        self.assertEqual(plan["total_fuel_cost"], "362.34")
        self.assertTrue(plan["estimated"])
        for leg in plan["legs"]:
            self.assertLessEqual(Decimal(leg["distance_miles"]), 500)
            self.assertGreaterEqual(Decimal(leg["arrival_fuel_gallons"]), 0)
            self.assertLessEqual(Decimal(leg["departure_fuel_gallons"]), 50)
            self.assertEqual(Decimal(leg["fuel_consumed_gallons"]), Decimal(leg["distance_miles"]) / 10)
        self.assertEqual(plan["legs"][-1]["arrival_fuel_gallons"], "0.00000000")

    def test_route_corridor_uses_segments_not_just_vertices(self) -> None:
        inside = self.city("Inside", -90, latitude=40.05)
        outside = self.city("Outside", -90, latitude=40.1)
        matches = project_cities(self.route, [inside, outside])
        self.assertIn(inside.pk, matches)
        self.assertNotIn(outside.pk, matches)
        self.assertEqual(matches[inside.pk][1], Decimal(500))
        self.assertLess(matches[inside.pk][0], 5)

    def test_excludes_canadian_stations_and_stations_outside_corridor(self) -> None:
        canadian = self.city("Canada", -90, country="CA", state="ON")
        self.station(canadian, 2, "1")
        far = self.city("Far", -90, latitude=41)
        self.station(far, 3, "1")
        with self.assertRaises(RoutingError) as error:
            make_fuel_plan(self.route, self.start, self.finish)
        self.assertEqual(error.exception.status_code, 422)

    def test_destination_station_cannot_repair_unreachable_final_leg(self) -> None:
        self.station(self.finish, 2, "1")
        with self.assertRaises(RoutingError):
            make_fuel_plan(self.route, self.start, self.finish)

    def test_origin_requires_listed_station_and_uses_cheapest(self) -> None:
        self.route["properties"]["distance_miles"] = 100
        self.station(self.start, 2, "2")
        plan = make_fuel_plan(self.route, self.start, self.finish)
        self.assertEqual(plan["fuel_stops"][0]["station"]["opis_id"], 2)
        self.assertEqual(plan["total_fuel_cost"], "20.00")
        TruckStop.objects.all().delete()
        with self.assertRaises(RoutingError):
            make_fuel_plan(self.route, self.start, self.finish)

    def test_zero_length_geometry_rejected(self) -> None:
        self.route["geometry"]["coordinates"] = [[-100, 40], [-100, 40]]
        with self.assertRaises(RoutingError) as error:
            make_fuel_plan(self.route, self.start, self.finish)
        self.assertEqual(error.exception.status_code, 502)

    def test_route_endpoint_returns_422_when_plan_infeasible(self) -> None:
        from unittest.mock import patch

        from .services import RouteResult

        with patch("routing.views.get_route", return_value=RouteResult(feature=self.route, cached=True)):
            response = APIClient().post(
                "/api/routes/", {"start_city_id": self.start.pk, "finish_city_id": self.finish.pk}, format="json"
            )
        self.assertEqual(response.status_code, 422)
        self.assertIn("500-mile range", response.data["detail"])
