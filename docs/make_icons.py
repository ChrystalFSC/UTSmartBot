"""Draw the iPhone home-screen icon (static/apple-touch-icon.png) from the same shapes
as static/favicon.svg.

Usage:
    python docs/make_icons.py

iOS rounds the corners itself, so this version fills the whole square (transparent
corners would turn black). Drawn at 4x and scaled down for smooth edges.
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "static" / "apple-touch-icon.png"
SIZE, SUPERSAMPLE = 180, 4
DARK, LIGHT, ACCENT, WHITE = (49, 47, 130), (64, 99, 188), (30, 136, 229), (255, 255, 255)


def main():
    px = SIZE * SUPERSAMPLE
    s = px / 64  # the SVG is drawn on a 64x64 grid

    def p(x, y):
        return (x * s, y * s)

    # Diagonal gradient background, like the SVG's linearGradient.
    image = Image.new("RGB", (px, px))
    pixels = image.load()
    for y in range(px):
        for x in range(px):
            t = (x + y) / (2 * (px - 1))
            pixels[x, y] = tuple(round(DARK[i] + (LIGHT[i] - DARK[i]) * t) for i in range(3))

    draw = ImageDraw.Draw(image)
    # Speech bubble with its tail.
    draw.rounded_rectangle([p(8, 12), p(56, 46)], radius=7 * s, fill=WHITE)
    draw.polygon([p(31, 45), p(20, 55), p(20, 45)], fill=WHITE)
    # Graduation cap: board, crown and tassel.
    draw.polygon([p(32, 17), p(49, 25), p(32, 33), p(15, 25)], fill=DARK)
    draw.polygon([p(22, 29), p(22, 35), p(42, 35), p(42, 29), p(32, 33.7)], fill=DARK)
    draw.chord([p(22, 30), p(42, 40)], start=0, end=180, fill=DARK)
    draw.line([p(46.5, 26.5), p(46.5, 35)], fill=ACCENT, width=round(2.6 * s))
    draw.ellipse([p(44.1, 34.1), p(48.9, 38.9)], fill=ACCENT)

    image.resize((SIZE, SIZE), Image.LANCZOS).save(OUT, optimize=True)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
