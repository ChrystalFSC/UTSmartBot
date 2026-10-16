"""Generate the README charts as SVG (light and dark versions).

Usage:
    python docs/make_charts.py

The numbers are the measurements recorded in the README (evaluation runs on
15-16 October 2026). Each chart is a single series of horizontal bars that start
at zero, with values labelled directly on the bars. Colours were checked with the
data-viz palette validator against GitHub's light (#ffffff) and dark (#0d1117) pages.
"""
from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parent / "images"

THEMES = {
    "light": {"bar": "#2a78d6", "text": "#1f2328", "muted": "#59636e", "axis": "#d1d9e0"},
    "dark": {"bar": "#3987e5", "text": "#f0f6fc", "muted": "#9198a1", "axis": "#3d444d"},
}
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"

CHARTS = {
    "chart-context-size": {
        "title": "Text sent to the AI with each question",
        "subtitle": "Search sends only the relevant passages instead of the whole knowledge base",
        "max": 362_161,
        "rows": [
            ("Before: whole knowledge base", 362_161, "362,161 characters"),
            ("After: top search passages", 14_986, "about 15,000 characters (96% less)"),
        ],
    },
    "chart-embedding-models": {
        "title": "Search hit rate by embedding model",
        "subtitle": "Share of evaluation questions whose answer was in the retrieved passages",
        "max": 100,
        "rows": [
            ("multilingual-e5-large (used)", 97, "97%  (33/34)"),
            ("Keyword search only", 91, "91%  (30/33)"),
            ("paraphrase-multilingual-MiniLM", 88, "88%  (30/34)"),
            ("potion-multilingual-128M", 82, "82%  (28/34)"),
        ],
    },
    "chart-providers": {
        "title": "Answers correct by chat provider (free tiers)",
        "subtitle": "Full 43-question evaluation, same search, one provider at a time",
        "max": 100,
        "rows": [
            ("OpenRouter free models", 100, "100%  (43/43)"),
            ("Gemini flash-lite", 98, "98%  (42/43)"),
            ("Groq gpt-oss-120b, before tool fixes", 79, "79%  (34/43)"),
        ],
    },
}

WIDTH, LABEL_W, PAD = 760, 270, 24
BAR_H, ROW_H, TOP = 14, 34, 78
VALUE_ROOM = 230  # space after the longest bar for its value label


def bar_path(x, y, w, h, r=4):
    """A bar anchored at x with only its data end (right side) rounded."""
    r = min(r, w / 2, h / 2)
    return (f"M{x},{y} H{x + w - r} Q{x + w},{y} {x + w},{y + r} V{y + h - r} "
            f"Q{x + w},{y + h} {x + w - r},{y + h} H{x} Z")


def render(chart, theme):
    t = THEMES[theme]
    rows = chart["rows"]
    height = TOP + ROW_H * len(rows) + 16
    plot_w = WIDTH - PAD - LABEL_W - VALUE_ROOM
    x0 = PAD + LABEL_W
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
        f'viewBox="0 0 {WIDTH} {height}" role="img" aria-labelledby="t d" font-family="{FONT}">',
        f'<title id="t">{escape(chart["title"])}</title>',
        "<desc id=\"d\">" + escape("; ".join(f"{label}: {text}" for label, _, text in rows)) + "</desc>",
        f'<text x="{PAD}" y="30" font-size="17" font-weight="600" fill="{t["text"]}">{escape(chart["title"])}</text>',
        f'<text x="{PAD}" y="52" font-size="13" fill="{t["muted"]}">{escape(chart["subtitle"])}</text>',
        f'<line x1="{x0}" y1="{TOP - 8}" x2="{x0}" y2="{TOP + ROW_H * len(rows) - 12}" '
        f'stroke="{t["axis"]}" stroke-width="1"/>',
    ]
    for i, (label, value, text) in enumerate(rows):
        y = TOP + i * ROW_H
        w = max(2, plot_w * value / chart["max"])
        parts += [
            f'<text x="{x0 - 12}" y="{y + BAR_H - 2}" font-size="13" text-anchor="end" '
            f'fill="{t["text"]}">{escape(label)}</text>',
            f'<path d="{bar_path(x0, y, w, BAR_H)}" fill="{t["bar"]}"/>',
            f'<text x="{x0 + w + 8}" y="{y + BAR_H - 2}" font-size="13" fill="{t["muted"]}">{escape(text)}</text>',
        ]
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main():
    OUT.mkdir(exist_ok=True)
    for name, chart in CHARTS.items():
        for theme in THEMES:
            (OUT / f"{name}-{theme}.svg").write_text(render(chart, theme), encoding="utf-8")
    print(f"wrote {len(CHARTS) * len(THEMES)} charts to {OUT}")


if __name__ == "__main__":
    main()
