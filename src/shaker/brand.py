"""SHAKER's own marks on top of the vendored ui module: the glass, the slogan, the bar vocabulary."""

SLOGAN = "Shaken, not stirred. From chaos to membrane, one seed at a time."
TAGLINE = "reproducible Martini 3 self-assembly ensembles that reveal how AMPs act"

# A martini glass with an olive on a pick, six rows tall to stand beside the block letters.
_GLASS = (
    "\u2572        \u2571    \u2571",
    "  \u2572~~~~~\u25cf~~~\u2571  ",
    "    \u2572     \u2571    ",
    "      \u2572 \u2571      ",
    "       \u2502       ",
    "     \u2500\u2500\u2534\u2500\u2500     ",
)
_ASCII = str.maketrans({"\u2572": "\\", "\u2571": "/", "\u2502": "|", "\u2500": "-",
                        "\u2534": "+", "\u25cf": "o"})


def glass(unicode: bool = True) -> tuple:
    """The glass, in box-drawing characters or plain ASCII."""
    return _GLASS if unicode else tuple(row.translate(_ASCII) for row in _GLASS)


# The pick and the olive, as (row, column) cells of the glass.
_PICK = {(0, 9), (1, 8)}


def glass_colours(glass_colour: str, pick_colour: str) -> dict:
    """Colour of every drawn cell: the glass in one colour, pick and olive in another."""
    return {
        (r, c): pick_colour if (r, c) in _PICK else glass_colour
        for r, row in enumerate(_GLASS) for c, char in enumerate(row) if char != " "
    }


# What each stage is called once it is done.
DONE = {
    "check": "bar is clean",
    "plan": "menu ready",
    "prepare": "ingredients ready",
    "build": "poured",
    "run": "served",
    "lipids": "on the shelf",
}
