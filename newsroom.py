"""Placeholder wire desk — turns raw results into headlines until real
stories are filed.

Real articles (the baseball gazette, eventually other leagues) always win;
these mechanical headlines fill the front page when none exist yet. Each
item carries a deterministic art seed so its pixel-art stand-in image is
stable across reloads.
"""
from __future__ import annotations
import zlib


def _verb(margin: float) -> str:
    if margin == 0:
        return "draw with"
    if margin >= 10:
        return "rout"
    if margin >= 5:
        return "pound"
    if margin >= 3:
        return "top"
    if margin >= 2:
        return "beat"
    return "edge"


def _item(sport: str, league: str, home: str, away: str,
          hs: float, as_: float, url: str, extra: str = "") -> dict:
    hs, as_ = float(hs), float(as_)
    if hs >= as_:
        w, l, ws, ls = home, away, hs, as_
    else:
        w, l, ws, ls = away, home, as_, hs
    margin = ws - ls
    headline = f"{w} {_verb(margin)} {l}, {ws:g}–{ls:g}"
    drama = ""
    if margin == 0:
        drama = "a stalemate"
    elif margin <= 2 and ws + ls >= 6:
        drama = "a one-possession finish"
    elif margin <= 1:
        drama = "the slimmest of margins"
    elif margin >= 10:
        drama = "a runaway"
    elif margin >= 5:
        drama = "a comfortable cushion"
    if extra == "Playoffs":
        lede_prefix = "Postseason wire — "
    elif extra:
        lede_prefix = f"{extra} report — "
    else:
        lede_prefix = ""
    lede = (f"{lede_prefix}{w} took down {l} {ws:g}–{ls:g}" +
            (f" in {drama}." if drama else "."))
    return {
        "sport": sport, "league": league, "url": url,
        "headline": headline, "extra": extra, "lede": lede,
        "margin": margin, "total": ws + ls,
        "home_name": home, "away_name": away,
        "home_score": f"{hs:g}", "away_score": f"{as_:g}",
        "home_won": hs >= as_,
        "art_seed": zlib.crc32(url.encode()),
    }


def build_wire(baseball_scores: list, viperball_scores: list,
               tennis_scores: list, briefs: int = 8,
               hockey_scores: list | None = None) -> dict:
    """{"lead": item|None, "briefs": [items]} from raw adapter scores."""
    items = []
    for g in baseball_scores:
        items.append(_item(
            "Baseball", "O27", g["home_name"], g["away_name"],
            g["home_score"], g["away_score"], f"/game/baseball/{g['id']}",
            extra="Playoffs" if g.get("is_playoff") else ""))
    for g in viperball_scores:
        items.append(_item(
            "Viperball", g["league"], g["home_name"], g["away_name"],
            g["home_score"], g["away_score"],
            f"/game/viperball/{g['save_key']}/{g['week']}/{g['matchup_key']}",
            extra=f"Week {g['week']}"))
    for g in tennis_scores:
        items.append(_item(
            "Tennis", g["league"], g["home_name"], g["away_name"],
            g["home_points"], g["away_points"],
            f"/game/tennis/{g['source']}/{g['id']}"))
    for g in (hockey_scores or []):
        items.append(_item(
            "Hockey", g["league"], g["home_name"], g["away_name"],
            g["home_score"], g["away_score"], f"/game/hockey/{g['id']}",
            extra="Playoffs" if g.get("is_playoff") else ""))
    if not items:
        return {"lead": None, "briefs": []}

    # Lead: playoff games first, then the wildest scoreline still in doubt.
    def drama(i: dict) -> tuple:
        return (i["extra"] == "Playoffs", i["total"] - 2 * i["margin"])
    lead = max(items, key=drama)
    rest = [i for i in items if i is not lead]

    # Round-robin the briefs across sports so one league can't flood the page.
    by_sport: dict[str, list] = {}
    for i in rest:
        by_sport.setdefault(i["sport"], []).append(i)
    picked, idx = [], 0
    while len(picked) < briefs and any(by_sport.values()):
        for sport in list(by_sport):
            if by_sport[sport]:
                picked.append(by_sport[sport].pop(0))
                if len(picked) >= briefs:
                    break
        idx += 1
    return {"lead": lead, "briefs": picked}


# ── Pixel-art stand-in images ────────────────────────────────────────────
# Deterministic mirrored sprites, sports-broadcast palettes. Served as SVG
# so there's nothing to generate or store — the seed IS the image.

_PALETTES = [
    ("#0b3d2e", "#7fb069", "#e6aa68", "#f4f1ea"),   # night grass
    ("#13294b", "#e8a33d", "#c8102e", "#f4f1ea"),   # under the lights
    ("#3a2618", "#c97b4a", "#88a0a8", "#f4f1ea"),   # clay court
    ("#1d3354", "#9ed8db", "#e63946", "#f4f1ea"),   # ice & siren
    ("#2d2a32", "#ddd92a", "#52489c", "#f4f1ea"),   # arena neon
    ("#1a472a", "#e0e722", "#ffffff", "#0b1f14"),   # turf glare
]


# Sport scenes, 14×9 pixel maps drawn over seeded field-noise backgrounds.
# Legend: '.' = noisy background, other chars index into the scene palette.
_SCENES = {
    "baseball": {
        # infield diamond: white bases, dirt basepaths, mound
        "bg": ("#1c5b35", "#247048"),
        "colors": {"w": "#f4f1ea", "d": "#c0834a", "m": "#a9713c"},
        "map": [
            "..............",
            "......w.......",
            ".....d.d......",
            "....d...d.....",
            "...w..m..w....",
            "....d...d.....",
            ".....d.d......",
            "......w.......",
            "..............",
        ],
    },
    "tennis": {
        # court from above: white lines, net, seeded-color ball
        "bg": ("#1f6e46", "#27815a"),
        "colors": {"l": "#f4f1ea", "n": "#d7d2c5", "b": "#e0e722"},
        "map": [
            "llllllllllllll",
            "l.....n......l",
            "l.l...n..l...l",
            "l.l...n..l.b.l",
            "l.l...n..l...l",
            "l.l...n..l...l",
            "l.l...n..l...l",
            "l.....n......l",
            "llllllllllllll",
        ],
    },
    "hockey": {
        # rink from above: blue lines, red center line, crease, puck
        "bg": ("#dfe9f2", "#eef4fa"),
        "colors": {"b": "#1d4ed8", "r": "#c8102e", "c": "#9ed8db", "p": "#10151c"},
        "map": [
            "..b....r....b.",
            "..b....r....b.",
            "..b...rrr...b.",
            "ccb...rpr...bc",
            "ccb...rrr...bc",
            "ccb...rrr...bc",
            "..b...rrr...b.",
            "..b....r....b.",
            "..b....r....b.",
        ],
    },
    "viperball": {
        # gridiron stripes and a loose ball
        "bg": ("#0b3d2e", "#14533f"),
        "colors": {"s": "#e8e4d8", "b": "#8a5a2b"},
        "map": [
            "..s...s...s...",
            "..s...s...s...",
            "..s...s...s...",
            "..s...s.b.s...",
            "..s...s...s...",
            "..s...s...s...",
            "..s...s...s...",
            "..s...s...s...",
            "..s...s...s...",
        ],
    },
}


def pixel_art_svg(seed: int, sport: str = "", cols: int = 14, rows: int = 9,
                  cell: int = 10) -> str:
    """Pixel scene as an SVG string, deterministic in `seed`.

    With a known sport, draws that sport's scene (diamond / court /
    gridiron) over a seeded noise field so every game still looks
    distinct; otherwise falls back to the mirrored abstract sprite."""
    import random
    rng = random.Random(seed)
    w, h = cols * cell, rows * cell
    rects = []
    scene = _SCENES.get((sport or "").lower())
    if scene:
        bg_a, bg_b = scene["bg"]
        for y in range(rows):
            row = scene["map"][y]
            for x in range(cols):
                ch = row[x]
                if ch == ".":
                    if rng.random() < 0.30:
                        rects.append(
                            f'<rect x="{x * cell}" y="{y * cell}" '
                            f'width="{cell}" height="{cell}" fill="{bg_b}"/>')
                else:
                    rects.append(
                        f'<rect x="{x * cell}" y="{y * cell}" '
                        f'width="{cell}" height="{cell}" '
                        f'fill="{scene["colors"][ch]}"/>')
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
            f'shape-rendering="crispEdges">'
            f'<rect width="{w}" height="{h}" fill="{bg_a}"/>' + "".join(rects) + "</svg>"
        )
    bg, c1, c2, c3 = _PALETTES[seed % len(_PALETTES)]
    half = cols // 2 + cols % 2
    for y in range(rows):
        for x in range(half):
            r = rng.random()
            if r < 0.42:
                continue
            color = c1 if r < 0.70 else c2 if r < 0.90 else c3
            for px in (x, cols - 1 - x):
                rects.append(
                    f'<rect x="{px * cell}" y="{y * cell}" '
                    f'width="{cell}" height="{cell}" fill="{color}"/>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'shape-rendering="crispEdges">'
        f'<rect width="{w}" height="{h}" fill="{bg}"/>' + "".join(rects) + "</svg>"
    )
