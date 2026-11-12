"""Build the SHAKER logo: the mark beside the wordmark and the slogan.

    python assets/logo/build_logo.py

Reads mark-light.svg and mark-dark.svg (from build_mark.py) and writes logo-light.svg and
logo-dark.svg next to this script. Standard library only.
"""
import re
from pathlib import Path

import wordmark

HERE = Path(__file__).resolve().parent
INK = {"light": ("#2A211B", "#6B625A"), "dark": ("#F2ECE6", "#A69B92")}
HEIGHT, GAP = 300, 44
FONT = "'IBM Plex Sans', 'Helvetica Neue', Helvetica, Arial, sans-serif"


def build(theme):
    mark = (HERE / f"mark-{theme}.svg").read_text()
    view = re.search(r'viewBox="([^"]+)"', mark).group(1)
    inner = mark[mark.index(">") + 1: mark.rindex("</svg>")]
    _, _, w, h = (float(v) for v in view.split())
    mark_width = HEIGHT * w / h
    x = mark_width + GAP
    ink, muted = INK[theme]
    top = (HEIGHT - wordmark.HEIGHT - 44) / 2
    # the slogan spans exactly the width of the wordmark: every letter gets its own position
    k = wordmark.WIDTH / wordmark.SLOGAN_WIDTH
    size = wordmark.SLOGAN_SIZE * k
    spans = "".join(f'<tspan x="{x + start * k:.1f}">{char}</tspan>'
                    for char, start in zip(wordmark.SLOGAN, wordmark.SLOGAN_STARTS) if char != " ")
    (HERE / f"logo-{theme}.svg").write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {x + wordmark.WIDTH + 24:.0f} {HEIGHT}" role="img" '
        'aria-label="SHAKER: Shaken chaos. Self-served order.">\n'
        f'<svg x="0" y="0" width="{mark_width:.1f}" height="{HEIGHT}" viewBox="{view}">{inner}</svg>\n'
        f'<path transform="translate({x:.0f} {top:.0f})" d="{wordmark.PATH}" fill="{ink}"/>\n'
        f'<text y="{top + wordmark.HEIGHT + 42:.0f}" font-family="{FONT}" font-size="{size:.2f}" '
        f'fill="{muted}">{spans}</text>\n</svg>\n'
    )


if __name__ == "__main__":
    for theme in INK:
        build(theme)
    print("wrote logo-light.svg and logo-dark.svg")
