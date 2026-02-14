import time
import requests
from django.core.management.base import BaseCommand
from routing.models import FuelStation


class Command(BaseCommand):
    help = "Geocode FuelStation records where latitude/longitude is NULL"

    NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
    REQUEST_DELAY = 1
    MAX_RETRIES = 3

    def handle(self, *args, **kwargs):

        self.stdout.write(self.style.SUCCESS("Starting geocoding process...\n"))

        stations = FuelStation.objects.filter(
            latitude__isnull=True
        ) | FuelStation.objects.filter(
            longitude__isnull=True
        )

        total = stations.count()

        if total == 0:
            self.stdout.write(
                self.style.SUCCESS("All stations already have coordinates.\n")
            )
            return

        updated_count = 0
        failed_count = 0

        headers = {
            "User-Agent": "fuel-route-optimizer-app"
        }

        for index, station in enumerate(stations, start=1):

            query = f"{station.address.strip()}, {station.city.strip()}, {station.state.strip()}, USA"

            for attempt in range(self.MAX_RETRIES):

                try:
                    response = requests.get(
                        self.NOMINATIM_URL,
                        headers=headers,
                        params={
                            "q": query,
                            "format": "json",
                            "limit": 1
                        },
                        timeout=15
                    )

                    if response.status_code == 429:
                        wait_time = 5 * (attempt + 1)
                        self.stdout.write(
                            self.style.WARNING(
                                f"[{index}/{total}] Rate limit hit. Sleeping {wait_time}s..."
                            )
                        )
                        time.sleep(wait_time)
                        continue

                    response.raise_for_status()
                    data = response.json()

                    if data:
                        station.latitude = float(data[0]["lat"])
                        station.longitude = float(data[0]["lon"])

                        station.save(update_fields=[
                            "latitude",
                            "longitude"
                        ])

                        updated_count += 1

                        self.stdout.write(
                            self.style.SUCCESS(
                                f"[{index}/{total}] Updated: {station.name} ({station.city})"
                            )
                        )
                    else:
                        failed_count += 1
                        self.stdout.write(
                            self.style.WARNING(
                                f"[{index}/{total}]  No result: {station.name}"
                            )
                        )

                    break

                except requests.exceptions.Timeout:
                    if attempt == self.MAX_RETRIES - 1:
                        failed_count += 1
                        self.stdout.write(
                            self.style.ERROR(
                                f"[{index}/{total}] Timeout: {station.name}"
                            )
                        )
                    else:
                        time.sleep(2)

                except requests.exceptions.RequestException as e:
                    if attempt == self.MAX_RETRIES - 1:
                        failed_count += 1
                        self.stdout.write(
                            self.style.ERROR(
                                f"[{index}/{total}] API Error: {station.name} | {str(e)}"
                            )
                        )
                    else:
                        time.sleep(2)

            time.sleep(self.REQUEST_DELAY)

        self.stdout.write("\n")
        self.stdout.write(
            self.style.SUCCESS(
                f"Geocoding completed: {updated_count} updated, {failed_count} failed."
            )
        )
