from main import mvg_api
from datetime import datetime


station_names = ["Klinikum Großhadern", "Max-Lebsche Platz"]
by_station = mvg_api(station_names, api_type="departures", combine_departures=False)
# Alle Abfahrten kombiniert und nach Zeit sortiert
all_deps = mvg_api(station_names, api_type="departures", combine_departures=True)
print("\nCombined & Sorted Departures:")
for dep in all_deps[:10]:  # nur die nächsten 10
    station_name = dep["_station_name"]
    line = dep.get("line") if isinstance(dep.get("line"), str) else dep.get("line", {}).get("name", "Unknown")
    dest = dep.get("destination", "Unknown")
    planned = datetime.fromtimestamp(dep.get("planned")).strftime("%H:%M")
    print(f"{planned} | {line} → {dest} | Station: {station_name}")