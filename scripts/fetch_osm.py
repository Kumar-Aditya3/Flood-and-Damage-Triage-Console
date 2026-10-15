"""Fetch OpenStreetMap context (named settlements + road network) for an event
AOI, so the triage output can name real affected communities and quantify
disrupted access instead of listing anonymous grid cells.

Writes ``data/samples/<event>/osm.json`` with:
  places: [{name, name_en, place, lat, lon}]   # city/town/village/hamlet
  roads:  [{id, highway, coords: [[lon,lat], ...]}]

OSM building footprints are largely unmapped in rural Pakistan, so impact is
derived from settlements + roads (both well covered here).

Usage:
    python scripts/fetch_osm.py --event sindh-town-floods-2022
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
import rasterio
from rasterio.warp import transform_bounds

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "data" / "samples"

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]
HEADERS = {"User-Agent": "flood-triage-prototype/1.0 (humanitarian triage demo)"}


def overpass(body: str) -> list[dict]:
    last = None
    for ep in ENDPOINTS:
        for _ in range(2):
            try:
                r = httpx.post(ep, data={"data": body}, headers=HEADERS, timeout=200)
                r.raise_for_status()
                return r.json().get("elements", [])
            except Exception as e:  # noqa: BLE001
                last = f"{ep}: {e.__class__.__name__}"
                time.sleep(2)
    raise RuntimeError(f"all Overpass endpoints failed ({last})")


def aoi_bounds(event_dir: Path):
    with rasterio.open(event_dir / "before.tif") as src:
        return transform_bounds(src.crs, "EPSG:4326", *src.bounds)  # w, s, e, n


def fetch(event: str) -> dict:
    event_dir = SAMPLES / event
    if not (event_dir / "before.tif").exists():
        sys.exit(f"no imagery for event '{event}' (run fetch_sample.py first)")
    w, s, e, n = aoi_bounds(event_dir)
    bbox = f"{s},{w},{n},{e}"
    print(f"AOI bbox (S,W,N,E) = {bbox}")

    print("  querying settlements ...")
    place_els = overpass(
        f'[out:json][timeout:120];'
        f'(node["place"~"city|town|village|hamlet"]({bbox}););out tags center;'
    )
    places = []
    for el in place_els:
        t = el.get("tags", {})
        name = t.get("name") or t.get("name:en")
        if not name:
            continue
        places.append({
            "name": name,
            "name_en": t.get("name:en") or _ascii_hint(name),
            "place": t.get("place", "village"),
            "lat": el.get("lat") or el.get("center", {}).get("lat"),
            "lon": el.get("lon") or el.get("center", {}).get("lon"),
        })

    print("  querying road network ...")
    road_els = overpass(
        f'[out:json][timeout:160];(way["highway"]({bbox}););out geom;'
    )
    roads = []
    for el in road_els:
        geom = el.get("geometry") or []
        if len(geom) < 2:
            continue
        roads.append({
            "id": el["id"],
            "highway": el.get("tags", {}).get("highway", "road"),
            "name": el.get("tags", {}).get("name"),
            "coords": [[g["lon"], g["lat"]] for g in geom],
        })

    out = {"bounds_wgs84": [w, s, e, n], "places": places, "roads": roads}
    (event_dir / "osm.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {event_dir / 'osm.json'}  ({len(places)} places, {len(roads)} roads)")
    return out


def _ascii_hint(name: str) -> str:
    """Fallback label when no name:en exists (keep original; UIs render UTF-8)."""
    return name


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True)
    a = ap.parse_args()
    fetch(a.event)
