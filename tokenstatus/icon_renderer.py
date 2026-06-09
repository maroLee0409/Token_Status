from __future__ import annotations

import io
from PIL import Image, ImageDraw, ImageFont


# Color thresholds (percent)
GREEN = (34, 197, 94)     # < 70%
AMBER = (245, 158, 11)    # 70 ~ 89%
RED   = (239, 68, 68)     # >= 90%
GREY  = (148, 163, 184)


def color_for(percent: float) -> tuple[int, int, int]:
    if percent >= 90:
        return RED
    if percent >= 70:
        return AMBER
    return GREEN


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("segoeuib.ttf", "arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def render_tray_icon(percent: float | None, size: int = 64) -> bytes:
    """Render a square tray icon as PNG bytes. Returns colored disc + bold % numeral."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if percent is None:
        color = GREY
        label = "—"
    else:
        color = color_for(percent)
        if percent >= 100:
            label = "!!"
        elif percent >= 10:
            label = f"{int(round(percent))}"
        else:
            label = f"{percent:.0f}"

    pad = 2
    draw.ellipse(
        [(pad, pad), (size - pad, size - pad)],
        fill=color + (255,),
        outline=(255, 255, 255, 230),
        width=max(1, size // 32),
    )

    # Pick the largest font that still fits horizontally with margin.
    target_w = size * 0.70
    fs = int(size * 0.62)
    font = _load_font(fs)
    while fs > 10:
        bbox = draw.textbbox((0, 0), label, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        if w <= target_w:
            break
        fs -= 2
        font = _load_font(fs)
    bbox = draw.textbbox((0, 0), label, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x = (size - w) / 2 - bbox[0]
    y = (size - h) / 2 - bbox[1] - 1
    draw.text((x, y), label, fill=(255, 255, 255, 255), font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
