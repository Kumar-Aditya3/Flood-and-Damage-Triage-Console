"""Headless layout/sanity assertions for the running UI (no image inspection)."""
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000/"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
event = sys.argv[1] if len(sys.argv) > 1 else "sindh-town-floods-2022"

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, headless=True)
    pg = b.new_page(viewport={"width": 1600, "height": 940})
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(BASE, wait_until="networkidle")
    pg.select_option("#eventSel", event)
    pg.wait_for_selector(".card", timeout=15000)
    pg.wait_for_timeout(1500)

    def box(sel):
        return pg.eval_on_selector(sel, "e => { const r = e.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height}; }")

    checks = {}
    checks["cards"] = pg.locator(".card").count()
    checks["markers"] = pg.locator(".cand-marker").count()
    checks["flooded_road_lines"] = pg.locator("path.leaflet-interactive").count()
    checks["summary_stats"] = pg.locator("#summary .stat").count()
    checks["first_card_name"] = pg.locator(".card .title .name").first.inner_text()
    checks["map_box"] = box("#map")
    checks["sidebar_box"] = box(".sidebar")
    checks["mapctl_box"] = box(".mapctl")
    # map canvas actually painted (leaflet tiles/overlay images loaded)
    checks["overlay_imgs"] = pg.locator(".leaflet-image-layer").count()
    # ensure sidebar and map do not overlap horizontally
    sb, mp = checks["sidebar_box"], checks["map_box"]
    checks["no_overlap"] = (sb["x"] + sb["w"]) <= mp["x"] + 2
    checks["map_fills"] = mp["w"] > 900 and mp["h"] > 700

    for k, v in checks.items():
        print(f"{k}: {v}")
    print("CONSOLE_ERRORS:", errs if errs else "none")
    b.close()

    ok = (checks["cards"] >= 5 and checks["markers"] >= 5 and checks["summary_stats"] == 3
          and checks["overlay_imgs"] >= 2 and checks["no_overlap"] and checks["map_fills"]
          and checks["flooded_road_lines"] > 0 and not errs)
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
