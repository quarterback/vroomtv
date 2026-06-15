"""The one durable postseason "content type" for the hub.

Every sport's adapter emits its bracket in this single normalized shape, and
``templates/playoffs.html`` renders any of them — regardless of how many rounds
there are, whether matchups are best-of-N series or single games, or how much
detail a sim records. New sports light up just by returning this shape; no
per-sport bracket code lives in the template or the route.

    bracket = {
        "label":    str,        # league label (the /playoffs dropdown entry)
        "tier":     str,        # "Pro" | "College" | "International"
        "season":   int|str,    # display only ("" if unknown)
        "champion": str,        # winning team name, or "" if undecided
        "rounds": [             # first round → final, in order
            {"name": str, "series": [series, ...]},
        ],
    }
    series = {
        "top": side, "bot": side|None,   # higher seed first; bot None = bye
        "best_of": int|None,             # series length (1 = single game)
        "winner": "top"|"bot"|"",        # which side advanced ("" = undecided)
    }
    side = {"name", "abbrev", "seed", "wins", "score"}
        wins  = games won in the series (shown when best_of > 1)
        score = points scored in a single game (shown when best_of == 1)

Adapters build sides/series with the helpers below so the shape stays uniform.
"""
from __future__ import annotations


def side(name, abbrev="", seed=None, wins=None, score=None):
    """One competitor in a matchup. Returns None for an absent side (a bye)."""
    if not name:
        return None
    return {"name": name, "abbrev": abbrev or "", "seed": seed,
            "wins": wins, "score": score}


def series(top, bot, best_of=None, winner=""):
    return {"top": top, "bot": bot, "best_of": best_of, "winner": winner}


def round_name(idx, total, by_conf=False):
    """Generic round label from its distance to the final, so any bracket size
    reads sensibly: a 2-round bracket is Semifinals → Finals; a 4-round one is
    First Round → … → Finals. ``by_conf`` names the conference-split rounds."""
    from_end = total - 1 - idx
    return {
        0: "Finals",
        1: "Conference Finals" if by_conf else "Semifinals",
        2: "Conference Semifinals" if by_conf else "Quarterfinals",
        3: "First Round",
    }.get(from_end, f"Round {idx + 1}")


def winner_by_wins(top, bot, best_of):
    """'top'/'bot'/'' once a side reaches the clinch number of a best-of-N."""
    if not best_of:
        return ""
    need = best_of // 2 + 1
    if top and (top.get("wins") or 0) >= need:
        return "top"
    if bot and (bot.get("wins") or 0) >= need:
        return "bot"
    return ""


def winner_by_score(top, bot):
    """'top'/'bot'/'' for a single decided game, by points."""
    if not top or not bot:
        return ""
    ts, bs = top.get("score"), bot.get("score")
    if ts is None or bs is None or ts == bs:
        return ""
    return "top" if ts > bs else "bot"


def champion_of(rounds):
    """The winner of the final round's single series, if decided."""
    if not rounds:
        return ""
    final = rounds[-1].get("series") or []
    if not final:
        return ""
    s = final[0]
    if s["winner"] == "top" and s.get("top"):
        return s["top"]["name"]
    if s["winner"] == "bot" and s.get("bot"):
        return s["bot"]["name"]
    return ""


def bracket(label, rounds, tier="Pro", season="", champion=""):
    return {"label": label, "tier": tier, "season": season,
            "champion": champion, "rounds": rounds}
