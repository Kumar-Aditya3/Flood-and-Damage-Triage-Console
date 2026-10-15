# Flood & Damage Triage Console

A lightweight, locally-runnable disaster-triage tool. It compares a **before**
and **after** satellite image pair, detects flooding, and produces a
**prioritized list of named communities** for a relief team to verify first —
each with the share of its footprint under water, the kilometres of road access
cut, before/after visual evidence, an honest uncertainty score, and a short
written field note.

It is built for a small relief organization to operate **without GIS
expertise**: open a browser, read the ranked list of villages/towns, click one,
look at the evidence, confirm or dismiss, export to GeoJSON/CSV for the field.

> SDG 11 (Sustainable Cities & Communities) · SDG 13 (Climate Action)

This is a **triage aid, not a validated damage classifier.** It tells a human
which communities to reach first and shows why, and every candidate carries a
confidence score. Written notes are explicitly labelled as model- or
heuristic-generated.

---

## Why this is useful (and different)

Change-detection on satellite imagery is an established research area. In a
catastrophic flood almost the entire scene is water, so "where is the water"
is not actionable — everything scores maximal. The useful question for a relief
team is **"which populated places are hit, and which roads into them are cut?"**

This tool answers that by fusing the flood signal with **OpenStreetMap** named
settlements and the road network, then ranking by an impact score that weights
flood exposure, road disruption, and settlement size. The result is a named,
differentiated worklist, e.g.:

```
 1  Khairpur Nathan Shah  town     SEVERE   66% footprint flooded · 12.2 km road cut
 2  Fatehpur              village  SEVERE   81% footprint flooded ·  4.4 km road cut
 ...
```

---

## What it does

1. **Detect new standing water** from the near-infrared band (water is dark in
   NIR; flooding turns previously-dry pixels dark), with de-speckling.
2. **Load OSM context** for the AOI: named settlements + the road network.
3. For each settlement, measure **flood exposure** in a radius scaled to its
   size, and the **length of nearby road now under water** (access disruption).
4. **Rank** settlements by an impact score, attach an **uncertainty** score, and
   crop before/after/overlay evidence chips.
5. Emit **flooded road segments** as GeoJSON so the map shows exactly which
   routes are cut.
6. **Describe** each candidate with a local vision model (Ollama) when available,
   or a deterministic metric-based note otherwise.
7. **Serve** a Leaflet triage UI with overlays, evidence, verification state and
   GeoJSON/CSV export.

---

## Requirements

- Windows (tested) / Linux / macOS, Python 3.13
- Internet access **once**, to fetch public sample imagery + OSM context
- *(Optional)* a cloud LLM API key for AI field notes (see `.env.example`)
- *(Optional)* [Ollama](https://ollama.com) + a vision model for local AI field notes

## Setup

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

### Configuration (`.env`)

Credentials are read from a `.env` file at the project root, which is
**gitignored**. Copy the committed template and fill it in:

```powershell
copy .env.example .env
```

| Variable | Purpose |
|----------|---------|
| `LLM_API_KEY` | Bearer token for the cloud LLM |
| `LLM_BASE_URL` | OpenAI-compatible endpoint or base URL |
| `LLM_MODEL` | Model id, e.g. `gpt-4o-mini` |
| `LLM_TIMEOUT` | *(optional)* seconds, default `60` |
| `LLM_TEMPERATURE` | *(optional)* default `0.2` |
| `OLLAMA_HOST` | *(optional)* local Ollama host, default `http://localhost:11434` |
| `VISION_MODEL` | *(optional)* Ollama vision model, default `moondream` |

`LLM_BASE_URL` accepts either a base URL or the full endpoint — both of these
resolve to the same call:

```
https://api.openai.com/v1
https://api.openai.com/v1/chat/completions
```

The app runs with **no** `.env` at all; field notes then fall back to the
deterministic heuristic.

## Fetch data (imagery + map context)

Public Sentinel-2 data (AWS Open Data) and OpenStreetMap (Overpass). Two 2022
Pakistan-floods samples are used by default:

```powershell
# before/after satellite pairs
.\.venv\Scripts\python scripts\fetch_sample.py --event pakistan-floods-2022  --lon 67.95  --lat 27.55 --size-m 12000
.\.venv\Scripts\python scripts\fetch_sample.py --event sindh-town-floods-2022 --lon 67.738 --lat 27.090 --size-m 12000

# named settlements + roads for each AOI
.\.venv\Scripts\python scripts\fetch_osm.py --event pakistan-floods-2022
.\.venv\Scripts\python scripts\fetch_osm.py --event sindh-town-floods-2022
```

## Run

```powershell
.\.venv\Scripts\python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. Analysis runs on first view and is cached under
`data/samples/<event>/analysis/`. Use **Re-run** to recompute.

### Keyboard
- `j` / `k` (or arrows): move through candidates · `Esc`: close evidence viewer

---

## AI field notes

Works fully without any model (grounded metric-based note fallback). When a
model *is* available, notes are generated by the first tier that responds:

1. **Cloud LLM** (preferred) — any OpenAI-compatible chat endpoint, configured
   via `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`. **Text-only:** the model
   receives the measured metrics as JSON, not the imagery, so it works with any
   chat model and keeps per-note cost minimal.
2. **Local Ollama vision model** — sends the before/after image chips plus the
   metrics to a multimodal model running on this machine:

   ```powershell
   ollama serve
   ollama pull moondream      # small; or llava / llama3.2-vision
   ```

3. **Deterministic heuristic** — always available, offline, no model.

The header pill shows which tier is active (`notes: <model>` for cloud,
`vision: <model>` for Ollama, `notes: heuristic` otherwise), and every stored
note records its own `source` so the operator can tell whether a model was
involved and which one.

`GET /api/vision/status` reports the active provider plus a per-provider
breakdown. To check the whole fallback chain without spending API credits:

```powershell
.\.venv\Scripts\python tools\_mock_llm.py          # terminal 1: stub endpoint
.\.venv\Scripts\python tools\_test_llm_path.py     # terminal 2: run the checks
```

---

## Architecture

```
scripts/fetch_sample.py   STAC search + windowed COG read -> aligned before/after GeoTIFFs
scripts/fetch_osm.py      Overpass -> named settlements + road network (osm.json)
backend/pipeline.py       NIR flood detection, settlement impact, road disruption, ranking, chips
backend/llm.py            OpenAI-compatible cloud LLM client (text-only, .env-configured)
backend/vision.py         Note generation: cloud LLM -> Ollama vision -> deterministic heuristic
backend/app.py            FastAPI: REST API + static front-end + GeoJSON/CSV export
frontend/                 Leaflet map, prioritized settlement list, evidence viewer (no build step)
scripts/screenshot.py     drives the app in a browser, captures docs/shots/*
scripts/verify_ui.py      headless layout/console sanity checks
```

### API

| Method | Path | Purpose |
|--------|------|---------|
| GET  | `/api/events` | list events |
| GET  | `/api/events/{event}` | cached analysis (runs if missing) |
| POST | `/api/events/{event}/analyze?force=true` | re-run the pipeline |
| POST | `/api/events/{event}/describe/{cid}` | vision/heuristic field note |
| POST | `/api/events/{event}/verify/{cid}?status=confirmed\|dismissed\|pending` | triage state |
| GET  | `/api/events/{event}/export.geojson` | candidates as GeoJSON (settlement footprints) |
| GET  | `/api/events/{event}/export.csv` | candidates as CSV |
| GET  | `/api/vision/status` | active field-note provider + per-provider breakdown |

Each candidate carries: `name` / `name_en`, `place_type`, `severity`,
`priority`, `uncertainty`, `flood_fraction`, `flooded_road_km`,
`road_disruption_frac`, `exposure_index`, `radius_m`, `lat`/`lon`.

---

## Method notes & honest limitations

- **New-water detection** uses a NIR threshold + a before→after NIR drop; robust
  for open-water flooding but can confuse water with deep shadow. De-speckling
  and a coherence term in the uncertainty score flag noisy detections.
- **Impact ranking** combines flood exposure, road disruption and a settlement
  size weight. The `exposure_index` (0–100) is a relative triage score, not a
  population count — no population raster is used, so people-affected figures are
  deliberately not invented.
- **OSM coverage** drives the output. Settlements and roads are well mapped in
  the sample AOIs; building footprints are largely absent in rural Pakistan, so
  impact is derived from settlements + roads rather than per-building counts.
- **Uncertainty** combines spatial coherence, NIR signal strength, and relative
  flooded area. It is a calibration aid for triage order, not a probability.
- Clouds/shadows are not explicitly masked; low-cloud scenes are chosen at fetch
  time (`--cloud`). For operational use, add a cloud mask, a population layer,
  and sub-pixel co-registration.

## Data & attribution

Contains modified Copernicus Sentinel data 2022, processed by ESA (free/open use
with attribution), read from the AWS Open Data `sentinel-cogs` bucket via the
Element84 `earth-search` STAC API. Map data © OpenStreetMap contributors (ODbL),
via the Overpass API.
