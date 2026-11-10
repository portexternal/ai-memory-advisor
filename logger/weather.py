"""Weather + location snapshot via free, keyless APIs (ip-api.com, open-meteo.com)."""
import json
import urllib.request
from pathlib import Path

LOCATION_CACHE = Path(__file__).resolve().parent.parent / "data" / "cache" / "location.json"
TIMEOUT = 8


def _get_json(url: str):
    with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def location():
    """Return {'lat','lon','city'} from cache or ip-api.com. Raises on failure."""
    if LOCATION_CACHE.exists():
        return json.loads(LOCATION_CACHE.read_text(encoding="utf-8"))
    data = _get_json("http://ip-api.com/json/?fields=lat,lon,city,status")
    if data.get("status") != "success":
        raise RuntimeError(f"ip-api failed: {data}")
    loc = {"lat": data["lat"], "lon": data["lon"], "city": data.get("city", "")}
    LOCATION_CACHE.parent.mkdir(parents=True, exist_ok=True)
    LOCATION_CACHE.write_text(json.dumps(loc), encoding="utf-8")
    return loc


def snapshot():
    """Current weather dict from open-meteo. Raises on failure."""
    loc = location()
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={loc['lat']}&longitude={loc['lon']}"
        "&current=temperature_2m,precipitation,weather_code,cloud_cover"
    )
    cur = _get_json(url).get("current", {})
    return {
        "city": loc["city"],
        "temp_c": cur.get("temperature_2m"),
        "precip_mm": cur.get("precipitation"),
        "weather_code": cur.get("weather_code"),
        "cloud_pct": cur.get("cloud_cover"),
    }
