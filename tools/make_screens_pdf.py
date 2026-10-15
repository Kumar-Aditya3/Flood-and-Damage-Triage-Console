"""Assemble the captured console screens into a single A4-landscape PDF.

Reads docs/screens/manifest.json (written by capture_screens.py) and produces
docs/Flood Triage Console - Application Screens.pdf with a cover page and one
captioned page per screen.
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "docs" / "screens"
OUT = ROOT / "docs" / "Flood Triage Console - Application Screens.pdf"

# A4 landscape at 150 dpi
PW, PH = 1754, 1240
M = 64

NAVY = (11, 27, 43)
CYAN = (34, 211, 238)
GREY = (90, 107, 122)
DARK = (11, 27, 43)
WHITE = (255, 255, 255)
BORDER = (214, 222, 229)

FONT_CANDIDATES = {
    "regular": [r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf"],
    "bold": [r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf"],
    "mono": [r"C:\Windows\Fonts\consola.ttf", r"C:\Windows\Fonts\cour.ttf"],
}


def font(kind: str, size: int):
    for p in FONT_CANDIDATES[kind]:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def wrap(draw, text, fnt, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=fnt) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def cover(manifest: dict) -> Image.Image:
    img = Image.new("RGB", (PW, PH), NAVY)
    d = ImageDraw.Draw(img)

    d.text((100, 300), "Flood & Damage Triage Console", font=font("bold", 62), fill=WHITE)
    d.rectangle([100, 392, 196, 399], fill=CYAN)
    d.text((100, 428), "Application screens", font=font("regular", 30), fill=(138, 166, 188))
    d.text(
        (100, 476),
        "Captured from the running console on the 2022 Sindh flood sample",
        font=font("regular", 22),
        fill=(138, 166, 188),
    )

    y = 580
    d.text((100, y), "CONTENTS", font=font("mono", 20), fill=CYAN)
    y += 44
    for i, s in enumerate(manifest["shots"], 1):
        d.text((100, y), f"{i:02d}", font=font("mono", 22), fill=CYAN)
        d.text((160, y), s["title"], font=font("regular", 24), fill=(230, 241, 248))
        y += 46

    d.text((100, PH - 110), manifest["base"], font=font("mono", 18), fill=(110, 138, 160))
    d.text((100, PH - 80), "7 screens", font=font("mono", 18), fill=(110, 138, 160))
    return img


def page(manifest: dict, shot: dict, index: int, total: int) -> Image.Image:
    img = Image.new("RGB", (PW, PH), WHITE)
    d = ImageDraw.Draw(img)

    d.text((M, 54), shot["title"], font=font("bold", 36), fill=DARK)
    d.rectangle([M, 108, M + 96, 114], fill=CYAN)

    # screenshot, scaled to fit and centred
    src = Image.open(shot["file"]).convert("RGB")
    target_w = 1560
    target_h = round(src.height * target_w / src.width)
    src = src.resize((target_w, target_h), Image.LANCZOS)
    x0 = (PW - target_w) // 2
    top = 142
    img.paste(src, (x0, top))
    d.rectangle([x0 - 1, top - 1, x0 + target_w, top + target_h], outline=BORDER, width=1)

    # caption
    cf = font("regular", 19)
    lines = wrap(d, shot["caption"], cf, target_w)
    cy = top + target_h + 30
    for ln in lines:
        d.text((x0, cy), ln, font=cf, fill=GREY)
        cy += 29

    d.text(
        (M, PH - 60),
        "Flood & Damage Triage Console — application screens",
        font=font("regular", 17),
        fill=(150, 163, 175),
    )
    pn = f"{index} / {total}"
    d.text(
        (PW - M - d.textlength(pn, font=font("mono", 17)), PH - 60),
        pn,
        font=font("mono", 17),
        fill=(150, 163, 175),
    )
    return img


def main() -> None:
    manifest = json.loads((SHOTS / "manifest.json").read_text(encoding="utf-8"))
    shots = manifest["shots"]
    pages = [cover(manifest)]
    for i, s in enumerate(shots, 1):
        pages.append(page(manifest, s, i, len(shots)))

    pages[0].save(
        OUT,
        "PDF",
        resolution=150.0,
        save_all=True,
        append_images=pages[1:],
    )
    size_mb = OUT.stat().st_size / 1024 / 1024
    print(f"wrote {OUT}")
    print(f"pages: {len(pages)}  ({len(shots)} screens + cover)")
    print(f"size: {size_mb:.2f} MB")


if __name__ == "__main__":
    main()
