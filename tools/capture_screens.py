"""Capture the triage console's key screens and emit a manifest for PDF assembly.

Drives the running app in a real browser (system Chrome, no chromium download)
and writes one PNG per screen to docs/screens/, plus manifest.json describing
the order, titles and captions used to build the PDF.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screens"
BASE = "http://127.0.0.1:8000/"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PRIMARY = "sindh-town-floods-2022"
SECONDARY = "pakistan-floods-2022"

shots: list[dict] = []


def capture(page, key: str, title: str, caption: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{key}.png"
    page.screenshot(path=str(path))
    shots.append({"key": key, "title": title, "caption": caption, "file": str(path)})
    print(f"  captured {key}")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 940}, device_scale_factor=2)
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(BASE, wait_until="networkidle")
        page.select_option("#eventSel", PRIMARY)
        page.wait_for_selector(".card", timeout=20000)
        page.wait_for_timeout(2000)

        # 1 — overview
        capture(
            page, "01_overview",
            "Overview — the triage worklist",
            "On load the console runs the flood pipeline and returns a ranked list of named settlements, "
            "each with its share of footprint flooded, kilometres of road cut and a confidence score. "
            "The map carries the flood-extent heat overlay, flooded road segments drawn in red, and "
            "settlement markers numbered by rank.",
        )

        # 2 — candidate selected
        page.locator('.card[data-id="c02"] .row1').click()
        page.wait_for_timeout(1600)
        capture(
            page, "02_selected",
            "Selecting a candidate",
            "Clicking a settlement flies the map to it and highlights its footprint. The card expands to "
            "show the metrics behind the rank — flood fraction, road disruption, exposure index and the "
            "analysis radius — so the ordering can be interrogated rather than trusted blindly.",
        )

        # 3 — evidence viewer
        page.locator('.card[data-id="c02"] .thumbs').click()
        page.wait_for_selector("#modal.open", timeout=8000)
        page.wait_for_timeout(900)
        capture(
            page, "03_evidence",
            "Evidence viewer — before / after / new water",
            "Every candidate carries a before/after image pair and a new-water overlay, cropped to the "
            "settlement. The generated field note sits underneath it and always records whether the text "
            "came from a model or from the deterministic metric-based fallback.",
        )
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)

        # 4 — AI field notes
        page.evaluate("document.querySelector('.cards').scrollTop = 0")
        page.wait_for_timeout(700)
        capture(
            page, "04_notes",
            "AI field notes",
            "With an OpenAI-compatible model configured, each candidate can be described in plain "
            "language grounded strictly in the measured metrics — no invented population or casualty "
            "figures. The header pill shows which provider is currently active, and each note is "
            "labelled with its own source.",
        )

        # 5 — before imagery
        page.locator('#baseSeg button[data-base="before"]').click()
        page.wait_for_timeout(1600)
        capture(
            page, "05_before",
            "Before imagery — the pre-flood baseline",
            "Toggling the base layer to Before shows the same area on 12 July 2022, ahead of the monsoon "
            "peak. Placing the two dates side by side is what makes the scale of new inundation legible "
            "to a non-specialist at a glance.",
        )

        # 6 — overlays isolated
        page.locator('#baseSeg button[data-base="after"]').click()
        page.wait_for_timeout(900)
        page.locator("#tgPriority").uncheck()
        page.locator("#tgFlood").check()
        page.locator("#tgPlaces").uncheck()
        page.wait_for_timeout(1600)
        capture(
            page, "06_overlays",
            "Overlays — isolating new water",
            "Overlays toggle independently. Here the flood-extent heat and settlement markers are off and "
            "the new-water mask is on, so only land that became water between the two acquisition dates "
            "is shown — the raw signal the ranking is built from.",
        )

        # restore toggles, then switch event
        page.locator("#tgPriority").check()
        page.locator("#tgPlaces").check()
        page.locator("#tgFlood").uncheck()
        page.select_option("#eventSel", SECONDARY)
        page.wait_for_timeout(2600)
        capture(
            page, "07_second_event",
            "A second area of interest",
            "The console is event-agnostic: the same pipeline runs on any before/after pair. Switching "
            "area re-ranks that region's settlements and redraws its flooded road network, with no "
            "reconfiguration and no code change.",
        )

        browser.close()

    manifest = {"base": BASE, "primary": PRIMARY, "secondary": SECONDARY, "shots": shots}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\n{len(shots)} screens written to {OUT}")
    if errors:
        print("CONSOLE ERRORS:")
        for e in errors:
            print("  -", e)
        return 2
    print("console errors: none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
