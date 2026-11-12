"""Build the SHAKER mark: the base illustration as vectors, plus the science.

    python assets/logo/build_mark.py

Reads source/illustration.png (drawn in the Figma identity file) and writes mark-light.svg and
mark-dark.svg next to this script. Needs the `brand` extra: vtracer, pillow, scipy, numpy.

The illustration is split by colour into line work, glass, drink, olive and pick, and each part
is traced to paths. The molecules are Martini beads, drawn as van der Waals spheres: water is
one bead, a lipid is a head, a glycerol bead and two tails of three, a peptide is a helix of
backbone beads with side chains. Every molecule is placed with two checks: it lies wholly
inside its vessel, with a margin to the line work, and it keeps a clearance from every other
molecule. The shaker holds the random mixture; the glass holds the bilayer.
"""
import math, re, shutil, tempfile
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage
import vtracer

import wordmark

HERE = Path(__file__).resolve().parent
SRC, OUT = HERE / "source" / "illustration.png", HERE
WORK = Path(tempfile.mkdtemp(prefix="shaker-mark-"))
K = 4                                   # working scale
PALETTE = {"bg": (229, 229, 229), "ink": (42, 33, 27), "rose": (224, 69, 127),
           "liquid": (252, 230, 238), "amber": (230, 161, 0), "red": (229, 72, 77)}
THEMES = {
    "light": dict(ink="#2A211B", rose="#E0457F", glass="#2A211B", liquid="#DCEAF6", liquid_opacity=1.0,
                  olive="#7C8F3A", pick="#7A5230", metal="#9A9189", engraving="#FBF8F5",
                  coral="#E5484D", backbone="#E8862F", backbone_far="#C2661A", sidechain="#F4B67E", glycerol="#B98E72",
                  tail="#DDD2C4", water="#7DB3D9", shine="#FFFFFF"),
    "dark": dict(ink="#F2ECE6", rose="#FF7EB6", glass="#F2ECE6", liquid="#6FB1EE", liquid_opacity=0.16,
                 olive="#A4B85A", pick="#B58A60", metal="#6E645C", engraving="#F2ECE6",
                 coral="#FF6B6B", backbone="#FF9F5A", backbone_far="#D9772E", sidechain="#FFC99C", glycerol="#C9A58A",
                 tail="#8C8178", water="#8CC6EE", shine="#FFFFFF"),
}

img = Image.open(SRC).convert("RGB")
big = np.asarray(img.resize((img.width * K, img.height * K), Image.LANCZOS)).astype(float)
names = list(PALETTE)
dist = np.stack([np.linalg.norm(big - np.array(PALETTE[n]), axis=-1) for n in names])
cls = dist.argmin(axis=0)
chroma = big.max(axis=-1) - big.min(axis=-1)
lum = big.mean(axis=-1)
greyish = (chroma < 34) & (lum < 216)
cls[greyish & (lum < 140)] = names.index("ink")
cls[greyish & (lum >= 140)] = names.index("bg")
mask = {n: cls == i for i, n in enumerate(names)}

# clean the classes: the blend between a rose line and the grey page is closest to the liquid
# colour, so the liquid keeps only its one large region; lines get that fringe back
liquid = ndimage.binary_opening(mask["liquid"], iterations=5)
lab, n = ndimage.label(liquid)
liquid = lab == (1 + np.argmax(ndimage.sum(liquid, lab, range(1, n + 1))))
fringe = mask["liquid"] & ~liquid
rose = mask["rose"] | (fringe & ndimage.binary_dilation(mask["rose"], iterations=3))
# the olive is the thick part of the amber shape, the pick is the thin rest
# The olive and its pick are measured from the illustration and redrawn as clean shapes; in
# the line work only the glass and the shaker are traced.
core = ndimage.binary_opening(mask["amber"], iterations=10)                 # the olive: the thick part
thin = mask["amber"] & ~ndimage.binary_dilation(core, iterations=6)          # the pick: the thin rest
def axes(m):
    ys_, xs_ = np.nonzero(m)
    pts_ = np.stack([xs_, ys_], axis=1).astype(float)
    centre = pts_.mean(axis=0)
    values, vectors = np.linalg.eigh(np.cov((pts_ - centre).T))
    return centre, vectors[:, 1], vectors[:, 0], 2 * np.sqrt(values[1]), 2 * np.sqrt(values[0]), pts_
olive_c, olive_u, olive_v, olive_a, olive_b, _ = axes(core)
pick_c, pick_u, _, _, _, pick_pts = axes(thin)
if pick_u[1] < 0:
    pick_u = -pick_u                                                          # point it down, into the drink
along = (pick_pts - olive_c) @ pick_u
pick_top, pick_bottom = olive_c + pick_u * along.min(), olive_c + pick_u * along.max()
blockers = ndimage.binary_dilation(mask["amber"], iterations=8)
drops = ndimage.binary_opening(mask["red"], iterations=2)      # the drops of the illustration, replaced by water

lines = {"ink": mask["ink"], "glass": rose}
# the removed drops leave a halo of blended pixels in the glass colour
lines["glass"] = lines["glass"] & ~ndimage.binary_dilation(mask["red"], iterations=7)
# the glass lines run unbroken where the pick used to cross them
near_pick = ndimage.binary_dilation(mask["amber"], iterations=30)
lines["glass"] = ndimage.binary_closing(lines["glass"], iterations=3) | (
    ndimage.binary_closing(lines["glass"] & near_pick, iterations=20) & near_pick)
lines["ink"] = ndimage.binary_closing(lines["ink"], iterations=1)

def tidy(m, smallest=900, sigma=2.2):
    """Drop the tiny islands colour blends leave behind, and take the raster wobble off the edge."""
    lab_m, n_m = ndimage.label(m)
    sizes = ndimage.sum(m, lab_m, range(1, n_m + 1))
    m = np.isin(lab_m, 1 + np.nonzero(sizes >= smallest)[0])
    return ndimage.gaussian_filter(m.astype(float), sigma) > 0.5
for name in lines:
    lines[name] = tidy(lines[name])
# where the glass touches the shaker the two outlines must stay joined, or the dome is open
both = lines["ink"] | lines["glass"]
lines["ink"] = lines["ink"] | (ndimage.binary_closing(both, iterations=9) & ~both)
liquid_fill = ndimage.binary_dilation(liquid, iterations=10)       # tuck under the line

def trace(m, name):
    Image.fromarray(np.where(m, 0, 255).astype(np.uint8)).save(str(WORK / f"{name}.png"))
    vtracer.convert_image_to_svg_py(str(WORK / f"{name}.png"), str(WORK / f"{name}.svg"), colormode="binary", mode="spline",
                                    filter_speckle=30, corner_threshold=75, length_threshold=8.0, path_precision=1)
    return re.findall(r'<path d="([^"]*)"[^>]*?transform="(translate\([^)]*\))"', (WORK / f"{name}.svg").read_text())

traced = {name: trace(m, name) for name, m in {**lines, "liquid": liquid_fill}.items()}

# ---- regions for placement ---------------------------------------------------------------
occupied = blockers.copy()
for m in lines.values():
    occupied |= m
free = ~ndimage.binary_dilation(occupied, iterations=2)
lab, _ = ndimage.label(free & ~liquid)
body = lab == lab[480 * K, 300 * K]                       # the white inside of the shaker body
body_dist = ndimage.distance_transform_edt(body)
dome = lab == lab[272 * K, 352 * K]                       # the inside of the domed top
dome_dist = ndimage.distance_transform_edt(dome)
# the cap and the band are the metal of the shaker: filled, not transparent
band = lab == lab[322 * K, 350 * K]
metal = ndimage.binary_dilation((lab == lab[203 * K, 382 * K]) | band, iterations=7)
_ys, _xs = np.nonzero(band)
_pts = np.stack([_xs, _ys], axis=1).astype(float)
band_centre = _pts.mean(axis=0)
_vals, _vecs = np.linalg.eigh(np.cov((_pts - band_centre).T))
band_dir = _vecs[:, 1] if _vecs[0, 1] > 0 else -_vecs[:, 1]          # along the band, left to right
band_angle = math.degrees(math.atan2(band_dir[1], band_dir[0]))
band_height = 4 * math.sqrt(_vals[0])                                 # across the band
traced["metal"] = trace(metal, "metal")
liquid_dist = ndimage.distance_transform_edt(liquid & free)
page = lab == lab[20, 20]                                  # the page around the drawing
page_dist = ndimage.distance_transform_edt(page)

def ok(dist, pts, margin):
    for x, y in pts:
        xi, yi = int(round(x)), int(round(y))
        if not (0 <= yi < dist.shape[0] and 0 <= xi < dist.shape[1]) or dist[yi, xi] < margin:
            return False
    return True


# ---- molecules, as Martini beads (van der Waals spheres) --------------------------------
M = 1.0                                     # one size for every molecule in the drawing
def bead(x, y, r, colour, t):
    """One coarse-grained bead: a flat sphere with a rim and a small shine."""
    return (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{colour}" stroke="{t["ink"]}" '
            f'stroke-opacity="0.55" stroke-width="{2.2 * M:.1f}"/>'
            f'<circle cx="{x - 0.32 * r:.1f}" cy="{y - 0.34 * r:.1f}" r="{0.26 * r:.1f}" fill="{t["shine"]}" fill-opacity="0.6"/>')

class Lipid:
    """Head bead, glycerol bead and two tails of three beads."""
    RH, RG, RT, TX = 10.5 * M, 7.5 * M, 7.2 * M, 7.6 * M
    G1, T0, TS = 14.5 * M, 27 * M, 12.6 * M
    LENGTH = T0 + 2 * TS + RT
    def __init__(self, p, angle, head):
        self.p, self.angle, self.head = np.array(p, float), angle, head
        a = math.radians(angle); u = np.array([math.cos(a), math.sin(a)])
        self.pts = [self.p + u * f * (self.LENGTH - self.RT) for f in (0, 0.33, 0.66, 1.0)]
        self.radius = self.TX + self.RT
    def svg(self, t):
        out = [f'<g transform="translate({self.p[0]:.1f} {self.p[1]:.1f}) rotate({self.angle - 90:.1f})">']
        for j in (2, 1, 0):
            for side in (-1, 1):
                out.append(bead(side * self.TX, self.T0 + j * self.TS, self.RT, t["tail"], t))
        out.append(bead(0, self.G1, self.RG, t["glycerol"], t))
        out.append(bead(0, 0, self.RH, t[self.head], t))
        return "".join(out) + "</g>"

class Peptide:
    """A helix of backbone beads, 3.6 per turn, seen from the side; side-chain beads point out."""
    RB, RS, RISE, AMP = 8.4 * M, 5.6 * M, 8.2 * M, 11.5 * M
    def __init__(self, p, angle, n):
        self.p, self.angle, self.n = np.array(p, float), angle, n
        self.length = (n - 1) * self.RISE
        a = math.radians(angle + 90); u = np.array([math.cos(a), math.sin(a)])
        self.pts = [self.p + u * f * self.length for f in np.linspace(-0.5, 0.5, max(5, n // 2))]
        self.radius = self.AMP + self.RB + 3 * M
    def svg(self, t):
        sites = []
        for i in range(self.n):
            th = math.radians(100 * i)
            sites.append((math.sin(th), math.cos(th), (i - (self.n - 1) / 2) * self.RISE, i))
        out = [f'<g transform="translate({self.p[0]:.1f} {self.p[1]:.1f}) rotate({self.angle:.1f})">']
        for sx, depth, y, i in sorted(sites, key=lambda q: q[1]):          # far side first
            if i % 3 == 0 and abs(sx) > 0.5:
                out.append(bead(sx * (self.AMP + self.RB + 2 * M), y, self.RS, t["sidechain"], t))
            out.append(bead(sx * self.AMP, y, self.RB, t["backbone_far"] if depth < -0.2 else t["backbone"], t))
        return "".join(out) + "</g>"

class Water:
    R = 9.2 * M
    def __init__(self, p, angle=0):
        self.p, self.pts, self.radius = np.array(p, float), [np.array(p, float)], self.R
    def svg(self, t):
        return bead(self.p[0], self.p[1], self.R, t["water"], t)

def gap(a, b):
    return min(np.linalg.norm(p - q) for p in a.pts for q in b.pts) - a.radius - b.radius

def place(rng, dist, margin, clearance, make, count, placed, candidates=500):
    """Best-candidate sampling: each new piece goes where it is farthest from the others."""
    ys, xs = np.nonzero(dist >= margin)
    done = []
    for _ in range(count):
        best, best_gap = None, -1
        for _ in range(candidates):
            i = rng.integers(len(xs))
            m = make((float(xs[i]), float(ys[i])), float(rng.uniform(0, 360)))
            if not ok(dist, m.pts, m.radius + margin):
                continue
            g = min([gap(m, o) for o in placed] + [1e9])
            if g >= clearance and (best is None or (g > best_gap and best_gap < 1e8)):
                best, best_gap = m, g
        if best is None:
            break
        placed.append(best); done.append(best)
    return done

def fill_water(rng, dist, margin, clearance, placed, limit, tries=9000):
    """Fill what is left of a region with water beads, keeping a clearance to everything."""
    ys, xs = np.nonzero(dist >= Water.R + margin)
    if len(xs) == 0:
        return []
    pts = np.array([q for m in placed for q in m.pts]).reshape(-1, 2)
    rad = np.array([m.radius for m in placed for _ in m.pts])
    done = []
    for i in rng.integers(len(xs), size=tries):
        if len(done) >= limit:
            break
        c = np.array([xs[i], ys[i]], float)
        if len(pts) and (np.linalg.norm(pts - c, axis=1) - rad - Water.R).min() < clearance:
            continue
        w = Water(c); done.append(w); placed.append(w)
        pts = np.vstack([pts, c]) if len(pts) else c[None, :]; rad = np.append(rad, Water.R)
    return done

rng = np.random.default_rng(20261112)
report = {}
heads = lambda: "coral" if rng.random() > 0.25 else "rose"

# ---- disorder in the shaker: peptides, lipids, and water as the most numerous species
shaker = []
sp = place(rng, body_dist, 9, 12, lambda p, a: Peptide(p, a, 13), 7, shaker, candidates=300)
sl = place(rng, body_dist, 8, 9, lambda p, a: Lipid(p, a, heads()), 34, shaker, candidates=260)
sw = fill_water(rng, body_dist, 6, 6, shaker, 400)
top = []
tp = place(rng, dome_dist, 9, 12, lambda p, a: Peptide(p, a, 11), 2, top, candidates=300)
tl = place(rng, dome_dist, 8, 9, lambda p, a: Lipid(p, a, heads()), 9, top, candidates=260)
tw = fill_water(rng, dome_dist, 6, 6, top, 200)
report["dome"] = dict(peptides=len(tp), lipids=len(tl), waters=len(tw))
report["shaker"] = dict(peptides=len(sp), lipids=len(sl), waters=len(sw),
                        min_gap=round(float(min(gap(a, b) for i, a in enumerate(shaker) for b in shaker[i + 1:])), 1))

# ---- order in the glass: a bilayer from wall to wall, one peptide through it, water on both sides
A, UP, RM = np.array([603.0, 405.0]) * K, np.array([-0.325, -0.945]), np.array([0.945, -0.325])
gp = lambda dd, kk: A + dd * UP + kk * RM
tdown = math.degrees(math.atan2(-UP[1], -UP[0]))
STEP, GAP, EDGE = 2 * Lipid.TX + 2 * Lipid.RT + 2.0 * M, 4 * M, 3
THICK = 2 * Lipid.LENGTH + GAP
def run(dd, ang, shift, side):
    """How many lipids fit in a row on one side of the peptide, with no gaps."""
    n = 0
    while n < 14 and ok(liquid_dist, Lipid(gp(dd, shift + side * (n + 1) * STEP), ang, "coral").pts, Lipid.RT + Lipid.TX + EDGE):
        n += 1
    return n
best = None
for shift in range(-70, 71, 5):
    for d_low in range(60, 330, 4):
        d_high = d_low + THICK
        n_pep = int(round((THICK + 30 * M) / Peptide.RISE)) + 1
        pep = Peptide(gp((d_low + d_high) / 2 + 0, shift), tdown - 90, n_pep)
        if not ok(liquid_dist, pep.pts, Peptide.RB + EDGE):
            continue
        counts = [run(d_high, tdown, shift, -1), run(d_high, tdown, shift, 1), run(d_low, tdown + 180, shift, -1), run(d_low, tdown + 180, shift, 1)]
        n_side = min(counts)
        if n_side < 2:
            continue
        counts = [n_side] * 4
        # as many pairs as fit, then as close to the axis of the glass and as centred in the drink as possible
        score = 4 * n_side - abs(shift) / 40 - abs(d_low - 190) / 300
        if best is None or score > best[0]:
            best = (score, d_low, d_high, shift, counts, n_pep)
glass = []
if best:
    _, d_low, d_high, shift, counts, n_pep = best
    for dd, ang, left, right in ((d_high, tdown, counts[0], counts[1]), (d_low, tdown + 180, counts[2], counts[3])):
        for k in list(range(-left, 0)) + list(range(1, right + 1)):
            glass.append(Lipid(gp(dd, shift + k * STEP), ang, "rose" if k % 4 == 0 else "coral"))
    glass.append(Peptide(gp((d_low + d_high) / 2, shift), tdown - 90, n_pep))
    gw = fill_water(rng, liquid_dist, 5, 6, glass, 200)
    report["glass"] = dict(upper=counts[0] + counts[1], lower=counts[2] + counts[3], sides=counts, d_low=d_low, shift=shift, waters=len(gw))
else:
    report["glass"] = "no bilayer fits"

# ---- the clink: short rays fanning up from the point where the glass touches the shaker,
# into the open wedge between the dome and the rim. Nothing else may be placed on them.
class Keepout:
    def __init__(self, p, radius):
        self.pts, self.radius = [np.array(p, float)], radius
    def svg(self, t):
        return ""
ys_c, xs_c = np.nonzero(lines["glass"])
contact = np.array([xs_c.min(), ys_c[xs_c.argmin()]], float)          # the leftmost point of the glass
clink = []
for angle, start, length in ((-103, 11, 10), (-82, 12, 15), (-61, 11, 10)):
    a_ = math.radians(angle); u_ = np.array([math.cos(a_), math.sin(a_)])
    clink.append((contact + u_ * start * K, contact + u_ * (start + length) * K))

# ---- the splash: the drops of the illustration become water beads, with a lipid and a peptide thrown up
air = [Keepout(q, 5 * K) for ray in clink for q in (ray[0], (ray[0] + ray[1]) / 2, ray[1])]
lab_d, n_d = ndimage.label(drops)
for cy, cx in ndimage.center_of_mass(drops, lab_d, range(1, n_d + 1)):
    air.append(Water((cx, cy)))
box = np.zeros_like(page); box[176 * K:266 * K, 426 * K:528 * K] = True
air_dist = np.where(box, page_dist, 0)
def first_fit(options, clearance):
    for m in options:
        if ok(page_dist, m.pts, m.radius + 8) and all(gap(m, o) >= clearance for o in air):
            air.append(m); return 1
    return 0
k4 = lambda x, y: (x * K, y * K)
n_pep = first_fit([Peptide(k4(503, 190), 62, 7), Peptide(k4(508, 196), 70, 7), Peptide(k4(498, 184), 55, 6)], 10)
n_lip = first_fit([Lipid(k4(462, 186), 285, "coral"), Lipid(k4(458, 192), 300, "coral")], 10)
n_lip += first_fit([Lipid(k4(531, 228), 215, "rose"), Lipid(k4(536, 222), 200, "rose"), Lipid(k4(526, 232), 230, "rose")], 10)
n_lip += first_fit([Lipid(k4(486, 232), 20, 'coral'), Lipid(k4(482, 238), 40, 'coral')], 8)
aw = fill_water(rng, air_dist, 8, 14, air, 14)
report["air"] = dict(drops_replaced=n_d, peptides=n_pep, lipids=n_lip, extra_waters=len(aw))

# ---- motion: the shaker is mid-shake, so arcs trail both of its long sides
def arcs(mid, along, normal, offsets):
    """Curved strokes parallel to one side of the shaker, shorter the farther they are."""
    along, normal = np.array(along) / np.linalg.norm(along), np.array(normal) / np.linalg.norm(normal)
    out = []
    for off, half in offsets:
        c = np.array(mid, float) * K + normal * off * K
        a, b, q = c - along * half * K, c + along * half * K, c + normal * 9 * K
        out.append((a, q, b))
    return out
motion = (arcs((250, 425), (-0.176, 0.984), (-0.984, -0.176), ((24, 62), (41, 44), (56, 26)))
          + arcs((376, 505), (-0.348, 0.937), (0.937, 0.348), ((24, 44), (40, 26))))
motion_pts = np.array([q for arc in motion for q in arc])

def olive_svg(t):
    """A green olive with its pimento, on a wooden pick that goes through it."""
    w = 3.4 * K
    angle = math.degrees(math.atan2(olive_u[1], olive_u[0]))
    rx, ry = olive_a * 1.08, olive_b * 1.12
    entry = olive_c - pick_u * rx * 0.58                    # where the pick goes in, and the pimento shows
    line = lambda a, b: (f'<path d="M{a[0]:.0f} {a[1]:.0f} L{b[0]:.0f} {b[1]:.0f}" fill="none" stroke="{t["pick"]}" '
                         f'stroke-width="{w:.1f}" stroke-linecap="round"/>')
    cx, cy = olive_c
    return "".join([
        line(pick_top, pick_bottom),
        f'<ellipse cx="{cx:.0f}" cy="{cy:.0f}" rx="{rx:.0f}" ry="{ry:.0f}" transform="rotate({angle:.1f} {cx:.0f} {cy:.0f})" '
        f'fill="{t["olive"]}" stroke="{t["ink"]}" stroke-opacity="0.55" stroke-width="{2.2 * M * 1.6:.1f}"/>',
        f'<ellipse cx="{cx - 0.30 * rx:.0f}" cy="{cy - 0.42 * ry:.0f}" rx="{0.26 * rx:.0f}" ry="{0.12 * ry:.0f}" '
        f'transform="rotate({angle:.1f} {cx:.0f} {cy:.0f})" fill="{t["shine"]}" fill-opacity="0.35"/>',
        f'<circle cx="{entry[0]:.0f}" cy="{entry[1]:.0f}" r="{ry * 0.30:.0f}" fill="{t["coral"]}" stroke="{t["ink"]}" '
        f'stroke-opacity="0.55" stroke-width="{2.2 * M:.1f}"/>',
        line(pick_top, entry),
    ])

# ---- write --------------------------------------------------------------------------------
ys, xs = np.nonzero(occupied)
allp = np.vstack([np.array([q for m in glass + shaker + top + air for q in m.pts]), motion_pts])
x0, y0 = int(min(xs.min(), allp[:, 0].min()) - 70), int(min(ys.min(), allp[:, 1].min()) - 70)
x1, y1 = int(max(xs.max(), allp[:, 0].max()) + 70), int(max(ys.max(), allp[:, 1].max()) + 70)
for theme, t in THEMES.items():
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0} {y0} {x1 - x0} {y1 - y0}" role="img" '
           'aria-label="SHAKER mark: a cocktail shaker holding a random mixture of coarse-grained beads meets a martini glass holding a bilayer">']
    for dd, tr in traced["liquid"]:
        out.append(f'<path d="{dd}" transform="{tr}" fill="{t["liquid"]}" fill-opacity="{t["liquid_opacity"]}"/>')
    out += [m.svg(t) for m in glass + shaker + top + air]
    for a, q, b in motion:
        out.append(f'<path d="M{a[0]:.0f} {a[1]:.0f} Q{q[0]:.0f} {q[1]:.0f} {b[0]:.0f} {b[1]:.0f}" fill="none" '
                   f'stroke="{t["ink"]}" stroke-opacity="0.5" stroke-width="{3.2 * K}" stroke-linecap="round"/>')
    for a, b in clink:
        out.append(f'<path d="M{a[0]:.0f} {a[1]:.0f} L{b[0]:.0f} {b[1]:.0f}" fill="none" stroke="{t["ink"]}" '
                   f'stroke-width="{2.8 * K}" stroke-linecap="round"/>')
    for dd, tr in traced["glass"]:
        out.append(f'<path d="{dd}" transform="{tr}" fill="{t["glass"]}"/>')
    out.append(olive_svg(t))
    for dd, tr in traced["metal"]:
        out.append(f'<path d="{dd}" transform="{tr}" fill="{t["metal"]}"/>')
    # the name and version on the band: the letters of the logo, as outlines, set along the band
    k = band_height * 0.46 / wordmark.BAND_HEIGHT
    out.append(f'<path d="{wordmark.BAND_PATH}" fill="{t["engraving"]}" '
               f'transform="translate({band_centre[0]:.0f} {band_centre[1]:.0f}) rotate({band_angle:.1f}) '
               f'scale({k:.3f}) translate({-wordmark.BAND_WIDTH / 2} {-wordmark.BAND_HEIGHT / 2})"/>')
    for dd, tr in traced["ink"]:
        out.append(f'<path d="{dd}" transform="{tr}" fill="{t["ink"]}"/>')
    out.append("</svg>")
    (OUT / f"mark-{theme}.svg").write_text("\n".join(out) + "\n")
print(report, "view", (x0, y0, x1 - x0, y1 - y0))
shutil.rmtree(WORK, ignore_errors=True)
