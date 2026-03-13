import httpx
import asyncio
from functools import lru_cache
from datetime import datetime, timedelta
import json

# Simple in‑memory cache with TTL
class RouteCache:
    def __init__(self, ttl_seconds: int = 60):
        self.ttl = ttl_seconds
        self.store = {}

    def get(self, key):
        entry = self.store.get(key)
        if not entry:
            return None
        value, expires_at = entry
        if datetime.utcnow() > expires_at:
            del self.store[key]
            return None
        return value

    def set(self, key, value):
        self.store[key] = (value, datetime.utcnow() + timedelta(seconds=self.ttl))

route_cache = RouteCache(ttl_seconds=60)

async def _call_external_service(url: str, params: dict, api_key: str | None = None):
    async with httpx.AsyncClient(timeout=10.0) as client:
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

async def get_best_routes(origin: str, destination: str, routing_url: str | None = None, api_key: str | None = None):
    cache_key = f"{origin}->{destination}"
    cached = route_cache.get(cache_key)
    if cached:
        return cached
    # If a dedicated routing service is configured, use it.
    if routing_url:
        params = {"origin": origin, "destination": destination, "mode": "transit"}
        try:
            data = await _call_external_service(routing_url, params, api_key)
        except Exception as e:
            print(f"Routing service error: {e}")
            return []
        routes = []
        for item in data.get("routes", []):
            route = {
                "line": item.get("line", "Unknown"),
                "departure_time": item.get("departure_time"),
                "wait_minutes": item.get("wait_minutes"),
                "total_travel_minutes": item.get("duration_min"),
                "transfer_at": item.get("transfer_station"),
            }
            routes.append(route)
        route_cache.set(cache_key, routes)
        return routes
    # Fallback: use existing MVG API departures to pick earliest matching line.
    try:
        from .main import mvg_api
        deps = await mvg_api([origin], api_type="departures", combine_departures=True)
        candidates = [d for d in deps if destination.lower() in (d.get("destination") or "").lower()]
        if not candidates:
            return []
        # Choose earliest departure
        now_ts = int(datetime.utcnow().timestamp())
        best = min(candidates, key=lambda d: max(0, d["planned"] - now_ts))
        route = {
            "line": best.get("line", "Unknown"),
            "departure_time": datetime.fromtimestamp(best["planned"]).isoformat(),
            "wait_minutes": int((best["planned"] - now_ts) / 60),
            "total_travel_minutes": None,
            "transfer_at": None,
        }
        route_cache.set(cache_key, [route])
        return [route]
    except Exception as e:
        print(f"Fallback routing error: {e}")
        return []
