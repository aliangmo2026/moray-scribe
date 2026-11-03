"""Render a terminal demo GIF from a real captured transcript.

The transcript below is copied from an actual run on this machine (timings are
the measured values from ``benchmarks/pipeline_profile.py``; the dashed-edge
count comes from the ``--infer`` run committed under ``docs/assets/flask_demo``).
Rendering it as an animated terminal keeps the demo dependency-free: no vhs, no
asciinema, no network.

Requires Pillow (a docs-only dependency, not needed to run moray-scribe):

    D:/python3.12/python.exe benchmarks/make_demo_gif.py
    # or, with Pillow installed in the active venv:
    python benchmarks/make_demo_gif.py --out docs/assets/demo.gif
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1120, 720
PAD = 26
LINE_HEIGHT = 26
FONT_SIZE = 17
BG = (11, 17, 32)
PANEL = (14, 22, 40)
BAR = (19, 29, 51)
TEXT = (214, 227, 245)
DIM = (126, 145, 175)
GREEN = (74, 222, 128)
CYAN = (56, 189, 248)
AMBER = (251, 191, 36)
RED = (248, 113, 113)

FONT_CANDIDATES = (
    "C:/Windows/Fonts/consola.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
)
# Consolas has no CJK glyphs; lines containing Chinese use this fallback.
CJK_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)

# (kind, text); kind drives color. Lines are revealed in order.
TRANSCRIPT: list[tuple[str, str]] = [
    ("cmd", "moray-scribe graph examples/flask_demo --out demo-out"),
    ("out", "demo-out\\pruned-graph.json"),
    ("blank", ""),
    ("cmd", "python benchmarks/pipeline_profile.py examples/flask_demo"),
    ("head", "== examples\\flask_demo =="),
    ("out", "lines: 243   peak RSS: 67.2 MB   total: 0.15s"),
    ("head", "stage        seconds   peak MB"),
    ("out", "scan           0.134      66.4"),
    ("out", "graph          0.003      66.9"),
    ("out", "prune          0.003      67.2"),
    ("out", "render         0.005      67.2"),
    ("blank", ""),
    ("cmd", "python web_viewer/serve.py demo-out/pruned-graph.json"),
    ("ok", "moray-scribe viewer: http://127.0.0.1:8765/"),
    ("dim", "  graph:  demo-out\\pruned-graph.json"),
    ("dim", "  source: examples"),
    ("blank", ""),
    ("cmd", "grep -c -- '-.->' docs/assets/flask_demo/diagram-00-flowchart.mmd"),
    ("amber", "5"),
    ("dim", "    # 5 dashed [AI推断] edges in the Mermaid flowchart"),
    ("blank", ""),
    ("ok", "done: static topology 0.15s · 8 routes · 4 data flows · 5 AI edges"),
]

COLOR_BY_KIND = {
    "cmd": TEXT,
    "out": DIM,
    "head": TEXT,
    "ok": GREEN,
    "dim": (90, 108, 138),
    "amber": AMBER,
    "blank": DIM,
}


def _font(size: int = FONT_SIZE) -> ImageFont.FreeTypeFont:
    for candidate in FONT_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _cjk_font(size: int = FONT_SIZE) -> ImageFont.FreeTypeFont:
    for candidate in CJK_FONT_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size, index=0)
            except OSError:  # pragma: no cover - font collection quirk
                continue
    return _font(size)


def _has_cjk(text: str) -> bool:
    return any(ord(char) > 0x2E80 for char in text)


def _frame_lines(revealed: int, typed: str | None, cursor_on: bool) -> list[tuple[str, str]]:
    """Transcript lines up to ``revealed``, with an optional typing line."""
    lines = TRANSCRIPT[:revealed]
    if typed is not None:
        lines = [*lines, ("cmd", typed + ("█" if cursor_on else ""))]
    return lines


def render_frame(font: ImageFont.FreeTypeFont, lines: list[tuple[str, str]]) -> Image.Image:
    cjk_font = _cjk_font()
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)

    # window chrome
    draw.rounded_rectangle((10, 10, WIDTH - 10, HEIGHT - 10), radius=14, fill=PANEL)
    draw.rounded_rectangle((10, 10, WIDTH - 10, 54), radius=14, fill=BAR)
    draw.rectangle((10, 40, WIDTH - 10, 54), fill=BAR)
    for index, color in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
        cx = 34 + index * 22
        draw.ellipse((cx - 6, 26 - 6, cx + 6, 26 + 6), fill=color)
    draw.text(
        (WIDTH / 2, 26),
        "moray-scribe  ·  static topology in milliseconds",
        font=font,
        fill=(139, 160, 194),
        anchor="mm",
    )

    y = 72
    for kind, text in lines:
        color = COLOR_BY_KIND.get(kind, TEXT)
        line_font = cjk_font if _has_cjk(text) else font
        if kind == "cmd":
            draw.text((PAD, y), "$", font=font, fill=GREEN)
            draw.text((PAD + 18, y), text, font=line_font, fill=color)
        else:
            draw.text((PAD + 18, y), text, font=line_font, fill=color)
        y += LINE_HEIGHT
        if y > HEIGHT - 56:
            break

    # footer status bar
    draw.line((PAD, HEIGHT - 50, WIDTH - PAD, HEIGHT - 50), fill=(28, 42, 68), width=1)
    draw.text(
        (PAD, HEIGHT - 38),
        "moray-scribe v0.3 · no CDN · local model optional",
        font=font,
        fill=(80, 97, 125),
    )
    return image


def build_frames() -> tuple[list[Image.Image], list[int]]:
    font = _font()
    frames: list[Image.Image] = []
    durations: list[int] = []

    def snap(lines, duration: int) -> None:
        frames.append(render_frame(font, lines))
        durations.append(duration)

    for index, (kind, text) in enumerate(TRANSCRIPT):
        if kind == "cmd":
            # type the command 3 characters per frame, cursor blinking
            step = 3
            for cut in range(0, len(text) + step, step):
                partial = text[:cut]
                snap(_frame_lines(index, partial, cut < len(text)), 30)
            snap(_frame_lines(index + 1, None, False), 120)
        elif kind == "blank":
            snap(_frame_lines(index + 1, None, False), 60)
        else:
            snap(_frame_lines(index + 1, None, False), 150 if kind != "ok" else 260)

    # final hold
    snap(_frame_lines(len(TRANSCRIPT), None, False), 1500)
    return frames, durations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the terminal demo GIF.")
    parser.add_argument("--out", type=Path, default=Path("docs/assets/demo.gif"))
    parser.add_argument("--colors", type=int, default=128, help="Palette size per frame.")
    args = parser.parse_args(argv)

    frames, durations = build_frames()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    first = frames[0].convert("P", palette=Image.Palette.ADAPTIVE, colors=args.colors)
    rest = [
        frame.convert("P", palette=Image.Palette.ADAPTIVE, colors=args.colors)
        for frame in frames[1:]
    ]
    first.save(
        args.out,
        save_all=True,
        append_images=rest,
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )
    size_mb = args.out.stat().st_size / (1024 * 1024)
    print(f"written: {args.out}  frames={len(frames)}  size={size_mb:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
