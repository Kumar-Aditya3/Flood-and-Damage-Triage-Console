"""Field notes for triage candidates, with a graceful three-tier fallback.

Order of preference:

  1. **Cloud LLM** (``backend.llm``) - an OpenAI-compatible chat model,
     text-only: it receives the measured metrics and returns a short note.
     Preferred when ``LLM_API_KEY`` / ``LLM_BASE_URL`` / ``LLM_MODEL`` are set.
  2. **Local Ollama vision model** - sends the before/after image chips plus the
     metrics to a locally-running multimodal model (``moondream`` by default).
  3. **Deterministic heuristic** - synthesized from the pipeline metrics so the
     tool stays fully functional offline.

The response always records which tier produced the text, so the operator knows
whether a model was involved and which one.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import httpx

from backend import llm

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_VISION_MODEL = "moondream"
_TIMEOUT = httpx.Timeout(120.0, connect=3.0)

PROMPT = (
    "You are assisting disaster-relief triage. Two satellite image crops of the "
    "same place are provided: BEFORE then AFTER a possible flood, centred on the "
    "settlement named below. A measured change summary is given. In 2-3 short "
    "sentences, describe what visibly changed around this settlement and whether "
    "it is consistent with flooding that would inundate the community or cut road "
    "access. Be concrete and cautious; do not invent specifics you cannot see. "
    "Metrics: {metrics}"
)

USER_PROMPT = (
    "Measured metrics for one settlement from the flood-detection pipeline "
    "(JSON):\n{metrics}\n\nWrite the 2-3 sentence triage note for this "
    "settlement."
)


def ollama_host() -> str:
    return (os.environ.get("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST).strip()


def vision_model() -> str:
    return (os.environ.get("VISION_MODEL") or DEFAULT_VISION_MODEL).strip()


def ollama_available() -> tuple[bool, str]:
    """Return (reachable, detail). Detail names the model or the problem."""
    host = ollama_host()
    model = vision_model()
    try:
        r = httpx.get(f"{host}/api/tags", timeout=httpx.Timeout(3.0))
        r.raise_for_status()
        names = [m.get("name", "") for m in r.json().get("models", [])]
        has = any(n.split(":")[0] == model.split(":")[0] for n in names)
        if not names:
            return False, "ollama running but no models pulled"
        if not has:
            return False, f"model '{model}' not pulled (have: {', '.join(names)})"
        return True, model
    except Exception as e:  # noqa: BLE001 - any failure means use fallback
        return False, f"ollama unreachable: {e.__class__.__name__}"


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _metrics(cand: dict) -> dict:
    return {
        "settlement": cand.get("name_en") or cand.get("name"),
        "settlement_type": cand.get("place_type"),
        "severity": cand.get("severity"),
        "priority": cand["priority"],
        "uncertainty": cand["uncertainty"],
        "flood_fraction_of_footprint": cand.get("flood_fraction"),
        "flooded_road_km": cand.get("flooded_road_km"),
        "road_disruption_fraction": cand.get("road_disruption_frac"),
        "exposure_index_0_100": cand.get("exposure_index"),
    }


def describe(event_dir: Path, cand: dict) -> dict:
    """Produce {text, source} for one candidate dict.

    Tries the cloud LLM, then local Ollama, then the deterministic heuristic.
    """
    metrics = _metrics(cand)
    reasons: list[str] = []

    # --- tier 1: cloud LLM (text-only) --------------------------------------
    ok, detail = llm.available()
    if ok:
        try:
            text = llm.complete(USER_PROMPT.format(metrics=json.dumps(metrics)))
            return {"text": text, "source": f"cloud:{llm.model()}"}
        except llm.LLMError as e:
            reasons.append(f"cloud LLM failed: {e}")
    else:
        reasons.append(detail)

    # --- tier 2: local Ollama vision ----------------------------------------
    ok, detail = ollama_available()
    if ok:
        try:
            payload = {
                "model": vision_model(),
                "prompt": PROMPT.format(metrics=json.dumps(metrics)),
                "images": [
                    _b64(event_dir / cand["before_chip"]),
                    _b64(event_dir / cand["after_chip"]),
                ],
                "stream": False,
                "options": {"temperature": 0.2},
            }
            r = httpx.post(f"{ollama_host()}/api/generate", json=payload, timeout=_TIMEOUT)
            r.raise_for_status()
            text = (r.json().get("response") or "").strip()
            if text:
                return {"text": text, "source": f"vision:{vision_model()}"}
            reasons.append("vision model returned empty text")
        except Exception as e:  # noqa: BLE001
            reasons.append(f"vision call failed: {e.__class__.__name__}")
    else:
        reasons.append(detail)

    # --- tier 3: deterministic heuristic ------------------------------------
    return {"text": _heuristic(metrics), "source": f"heuristic ({'; '.join(reasons)})"}


def _heuristic(m: dict) -> str:
    name = m.get("settlement") or "this location"
    ptype = m.get("settlement_type") or "settlement"
    ff = m.get("flood_fraction_of_footprint") or 0.0
    road = m.get("flooded_road_km") or 0.0
    unc = m.get("uncertainty") or 0.5
    sev = m.get("severity") or "Unknown"

    parts: list[str] = []
    if ff >= 0.25:
        parts.append(
            f"The after image shows widespread new standing water across the {ptype} of "
            f"{name}: about {round(ff*100)}% of its surrounding footprint is newly "
            f"inundated ({sev.lower()} exposure), where it was dry before."
        )
    elif ff >= 0.05:
        parts.append(
            f"New standing water has appeared around {name}, covering roughly "
            f"{round(ff*100)}% of the {ptype}'s footprint that was previously dry."
        )
    else:
        parts.append(
            f"Little open water is detected within {name} itself, but nearby access "
            f"appears affected."
        )

    if road >= 0.1:
        parts.append(
            f"About {road} km of road around {name} is under water, so access to the "
            f"{ptype} is likely disrupted and should be checked before dispatch."
        )
    else:
        parts.append("No major road submergence was detected in the immediate approach.")

    conf = "low" if unc > 0.6 else "moderate" if unc > 0.35 else "high"
    parts.append(f"Detection confidence is {conf} (uncertainty {unc}); verify on the ground.")
    return " ".join(parts)
