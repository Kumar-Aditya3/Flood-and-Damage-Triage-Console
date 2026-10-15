"""Drive the running triage app in a real browser: load it, exercise the UI,
and capture screenshots. Uses the system Chrome install (no chromium download).

Produces (under docs/shots/):
  01_overview.png     initial load, after-image + overlays, candidate list
  02_selected.png     a candidate selected (map flown in, cell highlighted)
  03_before.png       base toggled to the before image
  04_modal.png        evidence modal (before | after | new-water overlay)
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000/"
OUT = Path(__file__).resolve().parent.parent / "docs" / "shots"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def run(event: str) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 940}, device_scale_factor=2)
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(BASE, wait_until="networkidle")
        page.select_option("#eventSel", event)
        page.wait_for_selector(".card", timeout=15000)
        page.wait_for_timeout(1500)  # let imagery + overlays paint
        n_cards = page.locator(".card").count()
        print(f"cards rendered: {n_cards}")
        page.screenshot(path=str(OUT / "01_overview.png"))

        # Select candidate #2 (click its header row, not the thumbnails)
        page.locator('.card[data-id="c02"] .row1').click()
        page.wait_for_timeout(1200)
        # Generate / refresh its description via the UI (first action button)
        page.locator('.card[data-id="c02"] .actions .btn').first.click()
        page.wait_for_selector('.card[data-id="c02"] .desc:not(.empty)', timeout=30000)
        desc = page.locator('.card[data-id="c02"] .desc').inner_text()
        print("description:", desc[:160].replace("\n", " "))
        page.wait_for_timeout(600)
        page.screenshot(path=str(OUT / "02_selected.png"))

        # Open evidence modal from the thumbnails
        page.locator('.card[data-id="c02"] .thumbs').click()
        page.wait_for_selector("#modal.open", timeout=5000)
        page.wait_for_timeout(600)
        page.screenshot(path=str(OUT / "04_modal.png"))
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        # Toggle base to BEFORE
        page.locator('#baseSeg button[data-base="before"]').click()
        page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT / "03_before.png"))


        browser.close()
        if errors:
            print("CONSOLE/PAGE ERRORS:")
            for e in errors:
                print("  -", e)
            return 2
    print("screenshots written to", OUT)
    return 0


if __name__ == "__main__":
    ev = sys.argv[1] if len(sys.argv) > 1 else "sindh-town-floods-2022"
    sys.exit(run(ev))
