import requests
import polyline
from haversine import haversine, Unit

from rest_framework.views import APIView
from rest_framework.response import Response
from routing.models import FuelStation


class RouteAPIView(APIView):

    MPG = 10
    TANK_CAPACITY = 50
    MAX_RANGE = MPG * TANK_CAPACITY

    def post(self, request):

        start_name = request.data.get("start")
        end_name = request.data.get("end")

        if not start_name or not end_name:
            return Response({"error": "Start and end required"}, status=400)

        start = self.geocode_location(start_name)
        end = self.geocode_location(end_name)

        if not start or not end:
            return Response({"error": "Invalid location"}, status=400)

        route_data = self.get_route(start, end)

        if "routes" not in route_data:
            return Response({"error": "Route API failed"}, status=400)

        route = route_data["routes"][0]

        total_distance = route["distance"] * 0.000621371
        duration_hours = route["duration"] / 3600

        geometry = route["geometry"]
        route_coords = polyline.decode(geometry)

        fuel_stops, total_cost = self.calculate_optimal_stops(
            route_coords,
            total_distance
        )

        return Response({
            "distance_miles": round(total_distance, 2),
            "duration_hours": round(duration_hours, 2),
            "total_gallons_needed": round(total_distance / self.MPG, 2),
            "stops_required": len(fuel_stops),
            "total_fuel_cost": round(total_cost, 2),
            "fuel_stops": fuel_stops,
            "route_geometry": geometry
        })

    def get_route(self, start, end):

        start_lon, start_lat = start
        end_lon, end_lat = end

        url = (
            f"https://router.project-osrm.org/route/v1/driving/"
            f"{start_lon},{start_lat};{end_lon},{end_lat}"
            "?overview=full&geometries=polyline"
        )

        response = requests.get(url, timeout=20)
        response.raise_for_status()
        return response.json()

  
    def geocode_location(self, location_name):

        url = "https://nominatim.openstreetmap.org/search"

        params = {
            "q": location_name,
            "format": "json",
            "limit": 1
        }

        headers = {"User-Agent": "fuel-route-optimizer"}

        try:
            response = requests.get(url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()

            if data:
                return (float(data[0]["lon"]), float(data[0]["lat"]))
        except:
            return None

        return None

   
    def calculate_optimal_stops(self, route_coords, total_distance):

            route_points = []
            cumulative = 0
            route_points.append((route_coords[0][0], route_coords[0][1], 0))

            for i in range(1, len(route_coords)):
                prev = route_coords[i - 1]
                curr = route_coords[i]
                segment = haversine(prev, curr, unit=Unit.MILES)
                cumulative += segment
                route_points.append((curr[0], curr[1], cumulative))

            stations = FuelStation.objects.filter(
                latitude__isnull=False,
                longitude__isnull=False
            )

            station_points = []

            for station in stations:
                station_loc = (station.latitude, station.longitude)

                closest_mile = None
                min_dist = float("inf")

                for lat, lng, mile in route_points:
                    dist = haversine(station_loc, (lat, lng), unit=Unit.MILES)
                    if dist < min_dist:
                        min_dist = dist
                        closest_mile = mile

                if min_dist <= 20:
                    station_points.append({
                        "name": station.name,
                        "city": station.city,
                        "state": station.state,
                        "price": station.retail_price,
                        "latitude": station.latitude,
                        "longitude": station.longitude,
                        "mile": closest_mile
                    })

            station_points.sort(key=lambda x: x["mile"])

            fuel_remaining = self.TANK_CAPACITY
            current_position = 0
            total_cost = 0
            stops = []

            index = 0

            while current_position < total_distance:

                max_reach = current_position + fuel_remaining * self.MPG

             
                if max_reach >= total_distance:
                    break

           
                reachable = [
                    s for s in station_points
                    if current_position < s["mile"] <= max_reach
                ]

                if not reachable:
                    break

             
                cheapest = min(reachable, key=lambda x: x["price"])

          
                miles_to_station = cheapest["mile"] - current_position
                fuel_used = miles_to_station / self.MPG

                fuel_remaining -= fuel_used
                current_position = cheapest["mile"]

                remaining_trip = total_distance - current_position

               
                future = [
                    s for s in station_points
                    if current_position < s["mile"] <= current_position + self.MAX_RANGE
                ]

                cheaper_ahead = next((s for s in future if s["price"] < cheapest["price"]), None)

                if cheaper_ahead:
                    miles_to_cheaper = cheaper_ahead["mile"] - current_position
                    gallons_needed = (miles_to_cheaper / self.MPG) - fuel_remaining
                else:
                    if remaining_trip > self.MAX_RANGE:
                        gallons_needed = self.TANK_CAPACITY - fuel_remaining
                    else:
                        gallons_needed = (remaining_trip / self.MPG) - fuel_remaining

                gallons_needed = max(0, min(gallons_needed, self.TANK_CAPACITY))

                if gallons_needed <= 0:
                    continue  # no refill needed

                fuel_cost = gallons_needed * cheapest["price"]

                fuel_remaining += gallons_needed
                total_cost += fuel_cost

                stops.append({
                    "name": cheapest["name"],
                    "city": cheapest["city"],
                    "state": cheapest["state"],
                    "price_per_gallon": cheapest["price"],
                    "gallons_filled": round(gallons_needed, 2),
                    "fuel_cost": round(fuel_cost, 2),
                    "latitude": cheapest["latitude"],
                    "longitude": cheapest["longitude"],
                    "miles_from_start": round(current_position, 2)
                })

            return stops, total_cost
