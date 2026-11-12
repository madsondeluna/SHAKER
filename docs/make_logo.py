"""Draw the SHAKER logo as SVG: a martini glass in which scattered beads settle into two rows.

    .venv/bin/python docs/make_logo.py

Writes, for dark and light pages: logo-dark.svg, logo-light.svg (mark and wordmark) and
mark-dark.svg, mark-light.svg (the glass alone). Backgrounds are transparent.
"""

from pathlib import Path

DOCS = Path(__file__).resolve().parent

THEMES = {
    # ink is the wordmark and the beads; glass and pick are the two accents
    "dark": dict(ink="#F2ECE6", muted="#A69B92", glass="#FF7EB6", pick="#FFD166"),
    "light": dict(ink="#2A211B", muted="#6B625A", glass="#E0457F", pick="#E6A100"),
}

# Glass geometry in a 200 x 200 box.
CX, RIM_Y, APEX_Y, HALF = 100, 40, 132, 66
STEM_Y, BASE_HALF = 170, 30
STROKE = 4


def half_width(y):
    return HALF * (APEX_Y - y) / (APEX_Y - RIM_Y)


def row(y, spacing=11.0, radius=2.9):
    """Evenly spaced beads across the glass at height y."""
    reach = half_width(y) - 11
    count = int(reach // spacing)
    return [(CX + k * spacing, y, radius) for k in range(-count, count + 1)]


def mark(theme, x=0.0, y=0.0, scale=1.0):
    t = THEMES[theme]
    # chaos above: a few beads with no order
    scattered = [(70, 56, 2.4), (90, 52, 2.4), (110, 58, 2.4), (80, 68, 2.4), (101, 71, 2.4)]
    # order below: two rows, the two leaflets of a bilayer
    ordered = row(86) + row(98)
    beads = "".join(
        f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="{r}" fill="{t["ink"]}" '
        f'fill-opacity="{0.45 if (bx, by, r) in scattered else 1}"/>'
        for bx, by, r in scattered + ordered
    )
    return (
        f'<g transform="translate({x} {y}) scale({scale})">'
        f'{beads}'
        f'<g fill="none" stroke="{t["glass"]}" stroke-width="{STROKE}" stroke-linecap="round" '
        f'stroke-linejoin="round">'
        f'<path d="M{CX - HALF} {RIM_Y} L{CX} {APEX_Y} L{CX + HALF} {RIM_Y}"/>'
        f'<path d="M{CX} {APEX_Y} L{CX} {STEM_Y}"/>'
        f'<path d="M{CX - BASE_HALF} {STEM_Y} L{CX + BASE_HALF} {STEM_Y}"/></g>'
        f'<line x1="150" y1="22" x2="124" y2="64" stroke="{t["pick"]}" stroke-width="3" '
        f'stroke-linecap="round"/>'
        f'<circle cx="139" cy="40" r="7" fill="{t["pick"]}"/>'
        f'</g>'
    )


def write(theme):
    t = THEMES[theme]
    font = "'Helvetica Neue', Helvetica, Arial, sans-serif"
    (DOCS / f"mark-{theme}.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" role="img" '
        f'aria-label="SHAKER mark">\n{mark(theme)}\n</svg>\n'
    )
    (DOCS / f"logo-{theme}.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 760 200" role="img" '
        'aria-label="SHAKER: from chaos to membrane, one seed at a time">\n'
        f'{mark(theme)}\n'
        f'<text x="214" y="116" font-family="{font}" font-size="68" font-weight="300" '
        f'letter-spacing="20" fill="{t["ink"]}">SHAKER</text>\n'
        f'<text x="218" y="152" font-family="{font}" font-size="16.5" font-weight="400" '
        f'letter-spacing="1.6" fill="{t["muted"]}">From chaos to membrane, one seed at a time.'
        f'</text>\n'
        '</svg>\n'
    )


if __name__ == "__main__":
    for name in ("logo.svg", "logo-mark.svg"):  # earlier drafts
        (DOCS / name).unlink(missing_ok=True)
    for theme in THEMES:
        write(theme)
    print("wrote", ", ".join(sorted(p.name for p in DOCS.glob("*-dark.svg") )), "and light versions")
