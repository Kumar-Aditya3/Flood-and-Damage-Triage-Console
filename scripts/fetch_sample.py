"""Fetch a before/after Sentinel-2 L2A sample pair for a flood event.

Uses the public earth-search STAC API (Element84) and the AWS Open Data
sentinel-cogs bucket. No authentication required. Images are read as small
windows directly from the cloud-optimized GeoTIFFs, so downloads stay in the
low tens of megabytes.

Outputs per event directory:
  before.tif / after.tif   3-band uint8 true-color GeoTIFFs (10 m grid)
  before_nir.tif / after_nir.tif   float32 NIR reflectance (10 m grid)
  meta.json                scene IDs, dates, cloud cover, license, bounds
  preview.jpg              side-by-side preview for quick visual checks
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import rasterio
from rasterio import windows
from rasterio.crs import CRS
from rasterio.transform import from_bounds as affine_from_bounds
from rasterio.warp import transform as warp_transform
from rasterio.warp import transform_bounds

STAC_URL = "https://earth-search.aws.element84.com/v1/search"
ROOT = Path(__file__).resolve().parent.parent

BANDS = {"red": "B04", "green": "B03", "blue": "B02"}


def iso_range(date_range: str) -> str:
    """Expand date-only STAC ranges like '2022-07-01/2022-08-05' to full ISO datetimes."""
    parts = date_range.split("/")
    out = []
    for p in parts:
        p = p.strip()
        if "T" not in p:
            p += "T00:00:00Z"
        out.append(p)
    return "/".join(out)


def stac_search(bbox: list[float], date_range: str, max_cloud: float, limit: int = 60) -> list[dict]:
    body = {
        "collections": ["sentinel-2-l2a"],
        "bbox": bbox,
        "datetime": iso_range(date_range),
        "query": {"eo:cloud_cover": {"lt": max_cloud}},
        "limit": limit,
    }
    req = urllib.request.Request(
        STAC_URL,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        payload = json.loads(r.read())
    return payload.get("features", [])


def tile_code(item: dict) -> str:
    # S2B_42RUR_20220928_0_L2A -> 42RUR
    return item["id"].split("_")[1]


def pick_scene(features: list[dict]) -> dict | None:
    """Lowest cloud cover; ties broken by scene id for determinism."""
    if not features:
        return None
    return sorted(features, key=lambda f: (f["properties"]["eo:cloud_cover"], f["id"]))[0]


def read_band_window(href: str, win: windows.Window, out_shape: tuple[int, int]) -> np.ndarray:
    """Read a window from a remote COG, resized to out_shape if needed."""
    with rasterio.open(href) as src:
        data = src.read(1, window=win, boundless=True, fill_value=0)
    if data.shape != out_shape:
        data = cv2.resize(data, (out_shape[1], out_shape[0]), interpolation=cv2.INTER_LINEAR)
    return data


def shared_stretch(vals_a: np.ndarray, vals_b: np.ndarray) -> tuple[float, float]:
    """Percentile stretch computed over BOTH dates pooled, so the pair is rendered
    with identical tonemapping. Different per-date stretches would fabricate change."""
    pooled = np.concatenate([vals_a.ravel(), vals_b.ravel()])
    pooled = pooled[pooled > 0]  # drop nodata zeros
    lo, hi = np.percentile(pooled, [2, 98])
    if hi <= lo:
        return 0.0, 1.0
    return float(lo), float(hi)


def fetch_event(lon: float, lat: float, size_m: float, before_range: str, after_range: str,
                max_cloud: float, out_dir: Path) -> None:
    # Bounding box: size_m meters centered on (lon, lat), approximated in degrees.
    # Sentinel-2 L2A is delivered in UTM; we reproject precisely below.
    half_lat = (size_m / 2) / 111_320.0
    half_lon = (size_m / 2) / (111_320.0 * max(0.05, np.cos(np.deg2rad(lat))))
    bbox = [float(round(lon - half_lon, 5)), float(round(lat - half_lat, 5)),
            float(round(lon + half_lon, 5)), float(round(lat + half_lat, 5))]

    print(f"AOI bbox (EPSG:4326): {bbox}")
    before_feats = stac_search(bbox, before_range, max_cloud)
    after_feats = stac_search(bbox, after_range, max_cloud)
    print(f"scenes found: {len(before_feats)} before, {len(after_feats)} after")

    before = pick_scene(before_feats)
    if before is None:
        sys.exit("no before scene matched the filters")
    # Prefer an after scene from the same MGRS tile so the grids are natively aligned.
    same_tile = [f for f in after_feats if tile_code(f) == tile_code(before)]
    after = pick_scene(same_tile or after_feats)
    if after is None:
        sys.exit("no after scene matched the filters")

    print("before:", before["id"], f"cloud={before['properties']['eo:cloud_cover']:.1f}%")
    print("after: ", after["id"], f"cloud={after['properties']['eo:cloud_cover']:.1f}%")

    # Figure out the window once using the red band of the before scene.
    red_url = before["assets"]["red"]["href"]
    with rasterio.open(red_url) as src:
        epsg = src.crs.to_epsg()
        dst_crs = CRS.from_epsg(epsg)
        utm_bounds = transform_bounds("EPSG:4326", dst_crs, *bbox, densify_pts=21)
        win = windows.from_bounds(*utm_bounds, transform=src.transform)
        win = windows.Window(
            int(np.floor(win.col_off)), int(np.floor(win.row_off)),
            int(np.ceil(win.width)), int(np.ceil(win.height)),
        )
        win = win.round_offsets().round_lengths()
        out_transform = windows.transform(win, src.transform)

    h, w = int(win.height), int(win.width)
    print(f"window: {w}x{h} px at 10 m")

    out_dir.mkdir(parents=True, exist_ok=True)
    bands = {}
    for name in ("red", "green", "blue", "nir"):
        for tag, item in (("before", before), ("after", after)):
            key = f"{tag}_{name}"
            href = item["assets"].get(name, item["assets"].get("nir08", {})).get("href")
            if href is None:
                print(f"  missing asset {name} for {tag}, skipping")
                continue
            print(f"  reading {key} ...")
            bands[key] = read_band_window(href, win, (h, w)).astype(np.float32)

    if "before_red" not in bands or "after_red" not in bands:
        sys.exit("required RGB bands missing")

    out_meta = {
        "driver": "GTiff",
        "width": w,
        "height": h,
        "crs": dst_crs,
        "transform": out_transform,
        "compress": "deflate",
        "tiled": True,
    }

    rgb_profile = {**out_meta, "count": 3, "dtype": "uint8"}
    for tag in ("before", "after"):
        stack = []
        for name in ("red", "green", "blue"):
            a = bands[f"before_{name}"]
            b = bands[f"after_{name}"]
            lo, hi = shared_stretch(a, b)
            src_arr = bands[f"{tag}_{name}"]
            scaled = np.clip((src_arr - lo) / (hi - lo), 0, 1) * 255.0
            stack.append(scaled.astype(np.uint8))
        rgb = np.dstack(stack)  # H, W, 3
        path = out_dir / f"{tag}.tif"
        with rasterio.open(path, "w", **rgb_profile) as dst:
            for i in range(3):
                dst.write(rgb[:, :, i], i + 1)
        print(f"wrote {path}")

        nir_key = f"{tag}_nir"
        if nir_key in bands:
            nir_path = out_dir / f"{tag}_nir.tif"
            nir_prof = {**out_meta, "count": 1, "dtype": "float32", "nodata": 0.0}
            with rasterio.open(nir_path, "w", **nir_prof) as dst:
                dst.write(bands[nir_key] / 10_000.0, 1)
            print(f"wrote {nir_path}")

    bounds_wgs84 = list(transform_bounds(dst_crs, "EPSG:4326",
                                         out_transform.c, out_transform.f + h * out_transform.e,
                                         out_transform.c + w * out_transform.a, out_transform.f))

    meta = {
        "event": out_dir.name,
        "sensor": "Sentinel-2 MSI, Level-2A (bottom-of-atmosphere reflectance)",
        "source": "AWS Open Data sentinel-cogs bucket via Element84 earth-search STAC API",
        "before": {"id": before["id"], "datetime": before["properties"]["datetime"],
                   "cloud_cover": before["properties"]["eo:cloud_cover"]},
        "after": {"id": after["id"], "datetime": after["properties"]["datetime"],
                  "cloud_cover": after["properties"]["eo:cloud_cover"]},
        "bbox_wgs84": bounds_wgs84,
        "crs": f"EPSG:{epsg}",
        "pixel_size_m": 10.0,
        "attribution": "Contains modified Copernicus Sentinel data, processed by ESA",
        "license": "Copernicus Sentinel data 2022; free and open use with attribution",
    }
    with open(out_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    # Preview image for quick human sanity checks.
    with rasterio.open(out_dir / "before.tif") as b, rasterio.open(out_dir / "after.tif") as a:
        bi = np.dstack([b.read(i) for i in (3, 2, 1)])
        ai = np.dstack([a.read(i) for i in (3, 2, 1)])
    scale = 1000 / max(h, w)
    ph, pw = int(h * scale), int(w * scale)
    bi = cv2.resize(bi, (pw, ph), interpolation=cv2.INTER_AREA)
    ai = cv2.resize(ai, (pw, ph), interpolation=cv2.INTER_AREA)
    sep = np.full((ph, 4, 3), 255, np.uint8)
    cv2.imwrite(str(out_dir / "preview.jpg"), np.hstack([bi, sep, ai])[:, :, ::-1])
    print(f"wrote {out_dir / 'preview.jpg'}")
    print("done")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--event", default="pakistan-floods-2022", help="sample directory name")
    p.add_argument("--lon", type=float, default=67.95)
    p.add_argument("--lat", type=float, default=27.55)
    p.add_argument("--size-m", type=float, default=12000, help="AOI size in meters")
    p.add_argument("--before", default="2022-07-01/2022-08-05")
    p.add_argument("--after", default="2022-08-20/2022-09-30")
    p.add_argument("--cloud", type=float, default=15.0, help="max cloud cover percent")
    p.add_argument("--out", default=None, help="output directory (default data/samples/<event>)")
    args = p.parse_args()

    out_dir = Path(args.out) if args.out else ROOT / "data" / "samples" / args.event
    fetch_event(args.lon, args.lat, args.size_m, args.before, args.after, args.cloud, out_dir)


if __name__ == "__main__":
    main()
