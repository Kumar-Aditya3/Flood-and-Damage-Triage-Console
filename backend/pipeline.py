"""Impact-based flood triage pipeline.

The first version ranked anonymous grid cells by how much water appeared. In a
scene where almost everything floods, that is useless: every cell scores ~1.
This version answers the question a relief team actually asks:

    "Which named communities are flooded, and which roads into them are cut?"

Method
  1. Detect new standing water from the near-infrared band (water is dark in
     NIR; flooding turns previously-dry pixels dark).
  2. Load OpenStreetMap context (``osm.json``): named settlements and the road
     network for the AOI.
  3. For each settlement, measure flood exposure in a radius scaled to its size,
     and the length of road around it that is now under water (disrupted access).
  4. Rank settlements by an impact score (flood exposure x settlement size +
     road disruption), attach an uncertainty score, and crop before/after
     evidence chips.
  5. Emit flooded road segments as GeoJSON so the map shows exactly which routes
     are cut.

The output is a prioritized, *named* list with concrete numbers (percent of the
settlement footprint flooded, km of road submerged) and an honest confidence.
This is a triage aid, not a validated damage model.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import rowcol
from rasterio.warp import transform as warp_transform
from rasterio.warp import transform_bounds
from scipy import ndimage

# --- tunables (reflectance units, 0..1) -------------------------------------
WATER_NIR_MAX = 0.10
NIR_DROP_MIN = 0.045
CHANGE_SCALE = 0.45
MAX_CANDIDATES = 20
PX_M = 10.0  # Sentinel-2 pixel size

# per settlement-class: analysis radius (m), population weight, exposure weight
RADIUS_M = {"city": 2200, "town": 1500, "village": 700, "hamlet": 450,
            "suburb": 1000, "neighbourhood": 650}
POP_W = {"city": 1.0, "town": 1.0, "village": 0.55, "hamlet": 0.30,
         "suburb": 0.8, "neighbourhood": 0.6}
KEEP_FLOOD_FRAC = 0.05      # keep a settlement if this much of its footprint flooded
KEEP_ROAD_M = 150.0         # ...or this many metres of nearby road submerged
ROAD_FLOOD_RUN = 0.5        # a road segment counts as cut if >=50% of samples wet


def analyze_event(event_dir: Path, force: bool = False) -> dict:
    event_dir = Path(event_dir)
    out_dir = event_dir / "analysis"
    result_path = out_dir / "result.json"
    if result_path.exists() and not force:
        return json.loads(result_path.read_text(encoding="utf-8"))

    chips_dir = out_dir / "chips"
    chips_dir.mkdir(parents=True, exist_ok=True)

    before, after, nir_before, nir_after, crs, transform = _read_stack(event_dir)
    _, H, W = before.shape

    # --- flood signal --------------------------------------------------------
    water_before = nir_before < WATER_NIR_MAX
    water_after = nir_after < WATER_NIR_MAX
    nir_drop = nir_before - nir_after
    new_water = water_after & (~water_before) & (nir_drop > NIR_DROP_MIN)
    new_water_clean = ndimage.binary_opening(new_water, structure=np.ones((2, 2)))
    change = np.clip(np.sqrt(np.mean((after - before) ** 2, axis=0)) / CHANGE_SCALE, 0, 1)

    before_u8 = (np.transpose(before, (1, 2, 0)) * 255).astype(np.uint8)
    after_u8 = (np.transpose(after, (1, 2, 0)) * 255).astype(np.uint8)

    osm = _load_osm(event_dir)
    bounds = list(transform_bounds(crs, "EPSG:4326", *_bounds(event_dir)))

    # --- road disruption -----------------------------------------------------
    flooded_roads_geo, road_index = _analyze_roads(osm, crs, transform, new_water_clean, H, W)
    total_flooded_road_m = sum(seg["length_m"] for seg in flooded_roads_geo)

    # --- settlement impact ---------------------------------------------------
    cands = []
    for place in osm.get("places", []):
        c = _settlement_impact(place, crs, transform, new_water_clean, new_water,
                               nir_drop, road_index, H, W)
        if c is None:
            continue
        if c["flood_fraction"] >= KEEP_FLOOD_FRAC or c["flooded_road_m"] >= KEEP_ROAD_M:
            cands.append(c)

    # rank by impact; keep the most significant
    cands.sort(key=lambda d: d["impact_raw"], reverse=True)
    cands = cands[:MAX_CANDIDATES]
    max_raw = max((c["impact_raw"] for c in cands), default=1.0) or 1.0

    candidates = []
    for i, c in enumerate(cands):
        cid = f"c{i+1:02d}"
        rpx = c["radius_px"]
        r0, c0 = max(0, c["rc"] - rpx), max(0, c["cc"] - rpx)
        r1, c1 = min(H, c["rc"] + rpx), min(W, c["cc"] + rpx)
        before_chip = f"analysis/chips/{cid}_before.png"
        after_chip = f"analysis/chips/{cid}_after.png"
        overlay_chip = f"analysis/chips/{cid}_overlay.png"
        _save_chip(before_u8[r0:r1, c0:c1], event_dir / before_chip)
        _save_chip(after_u8[r0:r1, c0:c1], event_dir / after_chip)
        ov = _mask_tint(after_u8[r0:r1, c0:c1], new_water_clean[r0:r1, c0:c1], (40, 150, 255))
        _save_chip(ov, event_dir / overlay_chip)

        candidates.append({
            "id": cid, "rank": i + 1,
            "name": c["name"], "name_en": c["name_en"], "place_type": c["place"],
            "lat": c["lat"], "lon": c["lon"], "radius_m": c["radius_m"],
            "bbox_wgs84": c["bbox"],
            "flood_fraction": round(c["flood_fraction"], 3),
            "flooded_road_km": round(c["flooded_road_m"] / 1000.0, 2),
            "total_road_km": round(c["total_road_m"] / 1000.0, 2),
            "road_disruption_frac": round(c["road_disruption"], 3),
            "exposure_index": int(round(c["exposure_index"])),
            "priority": round(c["impact_raw"] / max_raw, 3),
            "uncertainty": c["uncertainty"],
            "severity": c["severity"],
            "category": c["category"],
            "before_chip": before_chip, "after_chip": after_chip,
            "overlay_chip": overlay_chip,
            "description": "", "description_source": "",
        })

    # --- map overlays + base images -----------------------------------------
    out_dir.mkdir(parents=True, exist_ok=True)
    Image.fromarray(before_u8).save(out_dir / "before.png")
    Image.fromarray(after_u8).save(out_dir / "after.png")
    heat = np.clip(new_water_clean.astype(np.float32) + 0.3 * change, 0, 1)
    _save_rgba(_mask_to_rgba(new_water_clean, (37, 150, 255)), out_dir / "flood_overlay.png")
    _save_rgba(_heat_to_rgba(heat), out_dir / "priority_overlay.png")
    _write_json(out_dir / "flooded_roads.geojson", {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": seg["coords"]},
            "properties": {"highway": seg["highway"], "name": seg["name"],
                           "length_m": round(seg["length_m"])},
        } for seg in flooded_roads_geo],
    })

    px_area_ha = (PX_M * PX_M) / 10_000.0
    affected = [c for c in candidates if c["flood_fraction"] >= KEEP_FLOOD_FRAC]
    meta = json.loads((event_dir / "meta.json").read_text(encoding="utf-8"))
    result = {
        "event": event_dir.name,
        "meta": meta,
        "bounds_wgs84": bounds,
        "image_size": {"width": W, "height": H},
        "layers": {
            "before": "analysis/before.png",
            "after": "analysis/after.png",
            "flood_overlay": "analysis/flood_overlay.png",
            "priority_overlay": "analysis/priority_overlay.png",
            "flooded_roads": "analysis/flooded_roads.geojson",
        },
        "places": [{"name": p["name"], "name_en": p["name_en"], "place": p["place"],
                    "lat": p["lat"], "lon": p["lon"]} for p in osm.get("places", [])
                   if p.get("lat") and p.get("lon")],
        "stats": {
            "settlements_in_aoi": len(osm.get("places", [])),
            "settlements_affected": len(affected),
            "total_flooded_road_km": round(total_flooded_road_m / 1000.0, 1),
            "new_water_hectares": round(int(new_water_clean.sum()) * px_area_ha, 0),
            "worst": candidates[0]["name_en"] if candidates else None,
        },
        "candidates": candidates,
    }
    _write_json(result_path, result)
    return result


# --- settlement + road analysis ---------------------------------------------
def _settlement_impact(place, crs, transform, nw_clean, nw_raw, nir_drop, road_index, H, W):
    lat, lon = place.get("lat"), place.get("lon")
    if lat is None or lon is None:
        return None
    rc, cc = _lonlat_to_rowcol(crs, transform, lon, lat)
    if not (0 <= rc < H and 0 <= cc < W):
        return None
    ptype = place.get("place", "village")
    radius_m = RADIUS_M.get(ptype, 700)
    rpx = max(6, int(round(radius_m / PX_M)))

    r0, c0 = max(0, rc - rpx), max(0, cc - rpx)
    r1, c1 = min(H, rc + rpx), min(W, cc + rpx)
    yy, xx = np.ogrid[r0:r1, c0:c1]
    circle = (yy - rc) ** 2 + (xx - cc) ** 2 <= rpx * rpx
    n_circle = int(circle.sum())
    if n_circle == 0:
        return None

    sub_clean = nw_clean[r0:r1, c0:c1][circle]
    sub_raw = nw_raw[r0:r1, c0:c1][circle]
    flood_fraction = float(sub_clean.mean())

    # roads within this settlement radius
    flooded_road_m, total_road_m = road_index.near(rc, cc, rpx)
    road_disruption = flooded_road_m / total_road_m if total_road_m > 1 else 0.0

    pop_w = POP_W.get(ptype, 0.5)
    impact_raw = (0.6 * flood_fraction + 0.4 * min(flooded_road_m / 2500.0, 1.0)) * pop_w
    exposure_index = 100.0 * flood_fraction * pop_w

    # uncertainty from coherence + signal strength + relative flooded area
    nw_px = int(sub_clean.sum())
    raw_px = int(sub_raw.sum())
    coherence = (nw_px / raw_px) if raw_px else 0.0
    drops = nir_drop[r0:r1, c0:c1][circle][sub_clean]
    strength = float(np.clip(((drops.mean() if drops.size else 0) - NIR_DROP_MIN) / 0.15, 0, 1))
    area = float(np.clip(nw_px / (0.15 * n_circle), 0, 1))
    confidence = 0.4 * coherence + 0.3 * strength + 0.3 * area
    uncertainty = round(float(np.clip(1 - confidence, 0.05, 0.95)), 2)

    bbox = _window_bounds(crs, transform, r0, c0, r1, c1)
    sev = _severity(flood_fraction)
    category = ("Flooded settlement" if flood_fraction >= KEEP_FLOOD_FRAC
                else "Access cut near settlement")

    return {
        "name": place["name"], "name_en": place.get("name_en") or place["name"],
        "place": ptype, "lat": lat, "lon": lon, "rc": rc, "cc": cc,
        "radius_m": radius_m, "radius_px": rpx, "bbox": bbox,
        "flood_fraction": flood_fraction,
        "flooded_road_m": flooded_road_m, "total_road_m": total_road_m,
        "road_disruption": road_disruption,
        "exposure_index": exposure_index, "impact_raw": impact_raw,
        "uncertainty": uncertainty, "severity": sev, "category": category,
    }


class _RoadIndex:
    """Pixel-space road samples, for fast 'roads within radius' queries."""
    def __init__(self, rows, cols, lengths, wet):
        self.rows = np.asarray(rows)
        self.cols = np.asarray(cols)
        self.len = np.asarray(lengths)
        self.wet = np.asarray(wet, dtype=bool)

    def near(self, rc, cc, rpx):
        if self.rows.size == 0:
            return 0.0, 0.0
        d2 = (self.rows - rc) ** 2 + (self.cols - cc) ** 2
        m = d2 <= rpx * rpx
        if not m.any():
            return 0.0, 0.0
        total = float(self.len[m].sum())
        flooded = float(self.len[m & self.wet].sum())
        return flooded, total


def _analyze_roads(osm, crs, transform, nw_clean, H, W):
    """Sample every road ~every 10 m; return flooded-segment GeoJSON + index."""
    roads = osm.get("roads", [])
    geo = []
    r_rows, r_cols, r_len, r_wet = [], [], [], []
    for road in roads:
        coords = road["coords"]
        if len(coords) < 2:
            continue
        lons = [p[0] for p in coords]
        lats = [p[1] for p in coords]
        xs, ys = warp_transform("EPSG:4326", crs, lons, lats)
        xs, ys = np.asarray(xs), np.asarray(ys)
        for k in range(len(coords) - 1):
            seg_len = float(np.hypot(xs[k + 1] - xs[k], ys[k + 1] - ys[k]))
            if seg_len <= 0:
                continue
            nstep = max(1, int(seg_len / PX_M))
            ts = (np.arange(nstep) + 0.5) / nstep
            sx = xs[k] + (xs[k + 1] - xs[k]) * ts
            sy = ys[k] + (ys[k + 1] - ys[k]) * ts
            rows, cols = rowcol(transform, sx, sy)
            rows = np.clip(np.asarray(rows), 0, H - 1)
            cols = np.clip(np.asarray(cols), 0, W - 1)
            wet = nw_clean[rows, cols]
            step_len = seg_len / nstep
            # record samples for the spatial index
            r_rows.extend(rows.tolist())
            r_cols.extend(cols.tolist())
            r_len.extend([step_len] * nstep)
            r_wet.extend(wet.tolist())
            # whole segment flooded if enough samples are wet
            if wet.mean() >= ROAD_FLOOD_RUN:
                geo.append({
                    "coords": [coords[k], coords[k + 1]],
                    "length_m": seg_len,
                    "highway": road["highway"], "name": road.get("name"),
                })
    return geo, _RoadIndex(r_rows, r_cols, r_len, r_wet)


def _severity(f):
    if f >= 0.5:
        return "Severe"
    if f >= 0.25:
        return "High"
    if f >= 0.1:
        return "Moderate"
    if f >= 0.03:
        return "Low"
    return "Minimal"


# --- raster / geo helpers ---------------------------------------------------
def _read_stack(event_dir: Path):
    with rasterio.open(event_dir / "before.tif") as src:
        before = src.read().astype(np.float32) / 255.0
        crs, transform = src.crs, src.transform
    with rasterio.open(event_dir / "after.tif") as src:
        after = src.read().astype(np.float32) / 255.0
    with rasterio.open(event_dir / "before_nir.tif") as src:
        nir_before = src.read(1).astype(np.float32)
    with rasterio.open(event_dir / "after_nir.tif") as src:
        nir_after = src.read(1).astype(np.float32)
    return before, after, nir_before, nir_after, crs, transform


def _bounds(event_dir: Path):
    with rasterio.open(event_dir / "before.tif") as src:
        return src.bounds


def _load_osm(event_dir: Path) -> dict:
    p = event_dir / "osm.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"places": [], "roads": []}


def _lonlat_to_rowcol(crs, transform, lon, lat):
    xs, ys = warp_transform("EPSG:4326", crs, [lon], [lat])
    r, c = rowcol(transform, xs[0], ys[0])
    return int(r), int(c)


def _window_bounds(crs, transform, r0, c0, r1, c1):
    x0, y0 = transform * (c0, r0)
    x1, y1 = transform * (c1, r1)
    w, s, e, n = transform_bounds(crs, "EPSG:4326",
                                  min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    return [w, s, e, n]


def _save_chip(arr: np.ndarray, path: Path, upscale_to: int = 320) -> None:
    if arr.size == 0:
        arr = np.zeros((8, 8, 3), np.uint8)
    img = Image.fromarray(arr)
    h, w = arr.shape[:2]
    scale = max(1, int(round(upscale_to / max(1, max(h, w)))))
    if scale > 1:
        img = img.resize((w * scale, h * scale), Image.NEAREST)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def _mask_tint(base_rgb, mask, color, alpha=0.5):
    out = base_rgb.astype(np.float32).copy()
    for i in range(3):
        out[..., i][mask] = out[..., i][mask] * (1 - alpha) + color[i] * alpha
    return np.clip(out, 0, 255).astype(np.uint8)


def _heat_to_rgba(heat):
    h = np.clip(heat, 0, 1)
    rgb = (np.dstack([np.ones_like(h), h, np.zeros_like(h)]) * 255).astype(np.uint8)
    alpha = (h * 200).astype(np.uint8)
    return np.dstack([rgb, alpha])


def _mask_to_rgba(mask, color):
    h, w = mask.shape
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[mask, 0], rgba[mask, 1], rgba[mask, 2], rgba[mask, 3] = (*color, 175)
    return rgba


def _save_rgba(rgba, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(path)


def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
