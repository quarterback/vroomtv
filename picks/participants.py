"""AI participant pool for Peak Fantasy Sports."""
from __future__ import annotations
import random
from . import db

_ADJECTIVES = [
    "Atomic", "Blazing", "Clutch", "Dagger", "Elite", "Fringe", "Grizzled",
    "Hammer", "Iron", "Janky", "Killer", "Lucky", "Money", "Nuclear", "Outlaw",
    "Pinch", "Quick", "Rogue", "Sharp", "Thunder", "Ultra", "Vicious", "Wild",
    "Xtreme", "Zealous", "Brick", "Copper", "Dusty", "Flash", "Ghost",
    "Heavy", "Icy", "Jade", "Kilo", "Laser", "Marble", "Nova", "Onyx",
    "Peak", "Quartz", "Rapid", "Silver", "Titan", "Urban", "Vapor", "Warp",
]
_NOUNS = [
    "Ace", "Arm", "Bat", "Bolt", "Cap", "Claw", "Cut", "Dime", "Dog",
    "Edge", "Eye", "Fan", "Flag", "Gem", "Gun", "Hand", "Hat", "Hit",
    "Hook", "Horn", "Jack", "Line", "Lock", "Mark", "Mitt", "Move",
    "Nail", "Odds", "Peg", "Pick", "Pin", "Play", "Post", "Pull",
    "Rail", "Ring", "Roll", "Rush", "Safe", "Shot", "Slam", "Slip",
    "Snap", "Spin", "Spot", "Star", "Stop", "Suit", "Tag", "Take",
    "Tide", "Tip", "Trap", "Turn", "Wave", "Whip", "Wire", "Zone",
]
_SUFFIXES = ["", "99", "42", "07", "88", "21", "55", "77", "11", "33", "X", "Pro", "Jr", "47", "64", "00"]

HUMAN_USERNAME = "Commissioner"
N_AI = 2000


def _gen_name(seed: int) -> str:
    rng = random.Random(seed)
    adj = rng.choice(_ADJECTIVES)
    noun = rng.choice(_NOUNS)
    suf = rng.choice(_SUFFIXES)
    return f"{adj}{noun}{suf}"


def ensure_participants() -> int:
    """Create human slot + AI pool if not yet done. Returns human participant id."""
    db.ensure_schema()

    human = db.fetchone("SELECT id FROM participants WHERE is_human=1")
    if not human:
        db.execute(
            "INSERT OR IGNORE INTO participants (username, is_human, skill_level) VALUES (?,1,0.60)",
            (HUMAN_USERNAME,)
        )
        human = db.fetchone("SELECT id FROM participants WHERE is_human=1")

    ai_count = db.fetchone("SELECT COUNT(*) AS c FROM participants WHERE is_human=0")["c"]
    if ai_count < N_AI:
        used_names = {r["username"] for r in db.fetchall("SELECT username FROM participants")}
        rows: list[tuple] = []
        seed = 0
        rng = random.Random(42)
        while len(rows) < N_AI - ai_count:
            name = _gen_name(seed)
            seed += 1
            if name in used_names:
                continue
            used_names.add(name)
            roll = rng.random()
            if roll < 0.20:
                skill = round(rng.uniform(0.62, 0.72), 3)
            elif roll < 0.70:
                skill = round(rng.uniform(0.46, 0.58), 3)
            else:
                skill = round(rng.uniform(0.35, 0.45), 3)
            rows.append((name, 0, skill))
        db.executemany(
            "INSERT OR IGNORE INTO participants (username, is_human, skill_level) VALUES (?,?,?)",
            rows
        )

    return db.fetchone("SELECT id FROM participants WHERE is_human=1")["id"]


def get_human_id() -> int:
    r = db.fetchone("SELECT id FROM participants WHERE is_human=1")
    if not r:
        return ensure_participants()
    return r["id"]
