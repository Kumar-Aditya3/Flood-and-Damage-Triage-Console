"""FastAPI application: serves the triage API and the static front-end.

Run from the project root:

    .venv\\Scripts\\python -m uvicorn backend.app:app --port 8000

Endpoints
    GET  /                              -> front-end
    GET  /api/events                    -> list available events
    GET  /api/events/{event}            -> cached analysis (runs if missing)
    POST /api/events/{event}/analyze    -> (re)run the pipeline (?force=true)
    POST /api/events/{event}/describe/{cid} -> vision/heuristic note for a candidate
    POST /api/events/{event}/verify/{cid}   -> record operator verification state
    GET  /api/events/{event}/export.geojson -> candidates as GeoJSON
    GET  /api/events/{event}/export.csv     -> candidates as CSV
    GET  /api/vision/status             -> active field-note provider (cloud/Ollama/heuristic)
    /data/...                           -> event imagery, chips, overlays
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from backend import llm, pipeline, vision

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "data" / "samples"
FRONTEND = ROOT / "frontend"

app = FastAPI(title="Flood & Damage Triage", version="1.0")


def _event_dir(event: str) -> Path:
    d = SAMPLES / event
    if not d.is_dir() or not (d / "meta.json").exists():
        raise HTTPException(404, f"unknown event '{event}'")
    return d


def _list_events() -> list[dict]:
    out = []
    if not SAMPLES.exists():
        return out
    for d in sorted(SAMPLES.iterdir()):
        meta_path = d / "meta.json"
        if d.is_dir() and meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            out.append({
                "event": d.name,
                "before_date": meta.get("before", {}).get("datetime"),
                "after_date": meta.get("after", {}).get("datetime"),
                "sensor": meta.get("sensor"),
                "analyzed": (d / "analysis" / "result.json").exists(),
            })
    return out


def _load_result(event: str, force: bool = False) -> dict:
    d = _event_dir(event)
    result = pipeline.analyze_event(d, force=force)
    # merge persisted descriptions + verification state
    _apply_overlays(d, result)
    return result


def _apply_overlays(d: Path, result: dict) -> None:
    desc = _read_json(d / "analysis" / "descriptions.json", {})
    verif = _read_json(d / "analysis" / "verifications.json", {})
    for c in result["candidates"]:
        if c["id"] in desc:
            c["description"] = desc[c["id"]]["text"]
            c["description_source"] = desc[c["id"]]["source"]
        c["verification"] = verif.get(c["id"], "pending")


def _read_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


@app.get("/api/events")
def api_events():
    return _list_events()


@app.get("/api/events/{event}")
def api_event(event: str):
    return _load_result(event)


@app.post("/api/events/{event}/analyze")
def api_analyze(event: str, force: bool = True):
    return _load_result(event, force=force)


@app.get("/api/vision/status")
def api_vision_status():
    """Report which field-note provider is active.

    Order mirrors ``vision.describe``: cloud LLM -> local Ollama -> heuristic.
    The top-level ``available``/``model``/``detail`` fields describe whichever
    provider is currently in effect; ``providers`` carries the full breakdown.
    """
    cloud_ok, cloud_detail = llm.available()
    ollama_ok, ollama_detail = vision.ollama_available()

    if cloud_ok:
        active, model, detail = "cloud", llm.model(), cloud_detail
    elif ollama_ok:
        active, model, detail = "ollama", vision.vision_model(), ollama_detail
    else:
        active, model, detail = "heuristic", None, f"{cloud_detail}; {ollama_detail}"

    return {
        "available": active != "heuristic",
        "model": model,
        "detail": detail,
        "active": active,
        "providers": {
            "cloud": {
                "available": cloud_ok, "model": llm.model(),
                "host": llm.host_label(), "detail": cloud_detail,
            },
            "ollama": {
                "available": ollama_ok, "model": vision.vision_model(),
                "host": vision.ollama_host(), "detail": ollama_detail,
            },
        },
    }


@app.post("/api/events/{event}/describe/{cid}")
def api_describe(event: str, cid: str):
    d = _event_dir(event)
    result = pipeline.analyze_event(d)
    cand = next((c for c in result["candidates"] if c["id"] == cid), None)
    if cand is None:
        raise HTTPException(404, f"unknown candidate '{cid}'")
    out = vision.describe(d, cand)
    store = _read_json(d / "analysis" / "descriptions.json", {})
    store[cid] = out
    _write_json(d / "analysis" / "descriptions.json", store)
    return out


@app.post("/api/events/{event}/verify/{cid}")
def api_verify(event: str, cid: str, status: str):
    if status not in {"pending", "confirmed", "dismissed"}:
        raise HTTPException(400, "status must be pending|confirmed|dismissed")
    d = _event_dir(event)
    store = _read_json(d / "analysis" / "verifications.json", {})
    store[cid] = status
    _write_json(d / "analysis" / "verifications.json", store)
    return {"id": cid, "verification": status}


@app.get("/api/events/{event}/export.geojson")
def api_export_geojson(event: str):
    result = _load_result(event)
    features = []
    for c in result["candidates"]:
        w, s, e, n = c["bbox_wgs84"]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[
                [w, s], [e, s], [e, n], [w, n], [w, s]
            ]]},
            "properties": {k: c[k] for k in (
                "id", "rank", "name", "name_en", "place_type", "priority",
                "uncertainty", "severity", "category", "flood_fraction",
                "flooded_road_km", "total_road_km", "road_disruption_frac",
                "exposure_index", "radius_m", "lat", "lon",
                "description", "description_source", "verification",
            ) if k in c},
        })
    fc = {"type": "FeatureCollection", "name": event, "features": features}
    return JSONResponse(fc, headers={
        "Content-Disposition": f'attachment; filename="{event}_candidates.geojson"'})


@app.get("/api/events/{event}/export.csv")
def api_export_csv(event: str):
    result = _load_result(event)
    cols = ["rank", "id", "name_en", "name", "place_type", "severity",
            "category", "priority", "uncertainty", "flood_fraction",
            "flooded_road_km", "total_road_km", "road_disruption_frac",
            "exposure_index", "lat", "lon", "radius_m", "verification",
            "description_source", "description"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for c in result["candidates"]:
        w.writerow(c)
    return Response(buf.getvalue(), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{event}_candidates.csv"'})


# --- static mounts ----------------------------------------------------------
app.mount("/data", StaticFiles(directory=str(SAMPLES)), name="data")


@app.get("/")
def index():
    return FileResponse(str(FRONTEND / "index.html"))


app.mount("/", StaticFiles(directory=str(FRONTEND)), name="frontend")
