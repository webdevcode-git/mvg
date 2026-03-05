import ssl
import certifi
from mvg import MvgApi
import aiohttp

# SSL Patch
ssl_context = ssl.create_default_context(cafile=certifi.where())
import mvg.mvgapi as mvgapi
original_get = aiohttp.ClientSession._request
def patched_request(self, method, url, **kwargs):
    kwargs["ssl"] = ssl_context
    return original_get(self, method, url, **kwargs)
aiohttp.ClientSession._request = patched_request

# ------------------------------
# Universelle MVG-API-Funktion
# ------------------------------
def mvg_api(stations, api_type="departures", combine_departures=False):
    if isinstance(stations, str):
        stations = [stations]
    
    results = {}
    combined = []

    for name in stations:
        # Suche Station und benutze globalId
        station_data = MvgApi.station(name)
        if not station_data:
            if combine_departures:
                continue
            else:
                results[name] = None
                continue

        # Manche Suchanfragen liefern mehrere Stationen, wir nehmen die erste
        station_id = station_data.get("id") or station_data.get("globalId")
        if not station_id:
            continue

        if api_type == "station_search":
            results[name] = station_data
        elif api_type == "departures":
            mvg_instance = MvgApi(station_id)
            departures = mvg_instance.departures()
            if combine_departures:
                for dep in departures:
                    dep["_station_name"] = station_data.get("name", name)
                    combined.append(dep)
            else:
                results[station_data.get("name", name)] = departures
        else:
            raise ValueError(f"Unbekannter api_type: {api_type}")

    if combine_departures:
        combined.sort(key=lambda x: x.get("planned", 0))
        return combined
    else:
        return results
