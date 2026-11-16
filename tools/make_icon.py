"""Draws the integration's icon, brand/icon.png and icon@2x.png.

The words STATIC RADIO in a hand-made 5x7 pixel font, amber on a dark
rounded square, so it reads on light and dark themes alike. It is this
project's own drawing and not Static's logo, which is Static's. Run:

    uv run python tools/make_icon.py custom_components/static_radio/brand
"""

import sys

from PIL import Image, ImageDraw

G: dict[str, list[str]] = {
    "S": [".####", "#....", "#....", ".###.", "....#", "....#", "####."],
    "T": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."],
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "I": [".###.", "..#..", "..#..", "..#..", "..#..", "..#..", ".###."],
    "C": [".####", "#....", "#....", "#....", "#....", "#....", ".####"],
    "R": ["####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"],
    "D": ["####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."],
    "O": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
}


def draw(size: int, out: str, bg: tuple[int, int, int, int], fg: tuple[int, int, int, int]) -> None:
    cell = size * 6 // 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = size // 8
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=bg)
    lines = ["STATIC", "RADIO"]
    h = (7 + 3 + 7) * cell
    y0 = (size - h) // 2
    for li, word in enumerate(lines):
        w = (len(word) * 6 - 1) * cell
        x0 = (size - w) // 2
        y = y0 + li * 10 * cell
        for ci, ch in enumerate(word):
            for ry, row in enumerate(G[ch]):
                for rx, px in enumerate(row):
                    if px == "#":
                        x = x0 + (ci * 6 + rx) * cell
                        yy = y + ry * cell
                        # 1px-ish gap between pixels for an LCD look
                        g = max(1, cell // 6)
                        d.rectangle([x, yy, x + cell - 1 - g, yy + cell - 1 - g], fill=fg)
    img.save(out)


dest = sys.argv[1]
BG, FG = (22, 22, 26, 255), (255, 176, 46, 255)
draw(256, f"{dest}/icon.png", BG, FG)
draw(512, f"{dest}/icon@2x.png", BG, FG)
