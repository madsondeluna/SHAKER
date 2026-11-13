"""SHAKER's own marks on top of the vendored ui module: the shaker, the slogan, the bar vocabulary."""

SLOGAN = "Shaken chaos. Self-served order."
TAGLINE = "reproducible Martini 3 self-assembly ensembles that reveal how AMPs act"

# A cobbler shaker as a solid silhouette (cap, strainer, tapered tin), built from the same block
# characters as the letters and six rows tall to stand beside them.
_SHAKER = (
    "   \u2584\u2584   ",
    "  \u259f\u2588\u2588\u2599  ",
    " \u2588\u2588\u2588\u2588\u2588\u2588 ",
    " \u2588\u2588\u2588\u2588\u2588\u2588 ",
    " \u2590\u2588\u2588\u2588\u2588\u258c ",
    "  \u2588\u2588\u2588\u2588  ",
)
_SHAKER_ASCII = (
    "   ..   ",
    "  .##.  ",
    " ###### ",
    " ###### ",
    " |####| ",
    "  ####  ",
)
_CAP_ROWS = 2  # the cap and the strainer take the second colour


def mark(unicode: bool = True) -> tuple:
    """The shaker, in block characters or plain ASCII."""
    return _SHAKER if unicode else _SHAKER_ASCII


def mark_colours(cap_colour: str) -> dict:
    """Colour of the cap and strainer; the tin keeps the gradient of the letters."""
    return {(r, c): cap_colour
            for r, row in enumerate(_SHAKER[:_CAP_ROWS]) for c, char in enumerate(row) if char != " "}


# What each stage is called once it is done.
DONE = {
    "check": "bar is clean",
    "plan": "menu ready",
    "prepare": "ingredients ready",
    "build": "poured",
    "run": "served",
    "lipids": "on the shelf",
}
