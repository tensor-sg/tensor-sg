"""
grind_core.py — shared rules for Commit Grind (used by the terminal hook and the GitHub Action).

Rarity is EARNED from what the commit is, not rolled at random:
  conventional type, message quality, tests next to code, focused size,
  net-deletion refactors, ML signals (new nn.Module, training loop), types/docstrings.
  A small seeded "lucky drop" can bump a commit one tier, but never into Mythic.
  Mythic needs a high score AND tests shipped with code.

Perks (CODM-style): unlocked by how you work, 3 slots (red / green / blue),
equip one per slot via a loadout. They boost XP or luck; they never change the score.
"""
import hashlib
import re
from pathlib import PurePosixPath

# ---------------------------------------------------------------- rarity & loot
RARITY_ORDER = ["Common", "Uncommon", "Rare", "Epic", "Legendary", "Mythic"]
RARITY_XP = {"Common": 10, "Uncommon": 20, "Rare": 40, "Epic": 80, "Legendary": 150, "Mythic": 300}
RARITY_HEX = {"Common": "#9ca3af", "Uncommon": "#4ade80", "Rare": "#60a5fa",
              "Epic": "#c084fc", "Legendary": "#facc15", "Mythic": "#f87171"}
RARITY_ANSI = {"Common": "grey", "Uncommon": "green", "Rare": "blue",
               "Epic": "purple", "Legendary": "gold", "Mythic": "red"}
THRESHOLDS = [(13, "Mythic"), (10, "Legendary"), (7, "Epic"), (4, "Rare"), (2, "Uncommon")]

LOOT = {
    "Common": ["Rusty Semicolon", "Stale Cache", "Print Debugger", "Lint Warning",
               "Untyped Variable", "TODO Comment", "Requirements.txt"],
    "Uncommon": ["Batch Normalizer", "Dropout Mask", "Learning Rate Scheduler",
                 "Docker Layer", "Clean Docstring", "Type Hint Charm"],
    "Rare": ["Attention Head", "Residual Connection", "Gradient Clipper",
             "Mixed Precision Core", "Vector Index Shard"],
    "Epic": ["Flash Attention Kernel", "LoRA Adapter", "Quantized Weights (int4)",
             "Graph Message Passer"],
    "Legendary": ["Golden Gradient", "Paraconsistent Logic Gate", "Transformer Crown"],
    "Mythic": ["Infinite Context Window", "Zero-Loss Convergence"],
}
TOTAL_ITEMS = sum(len(v) for v in LOOT.values())

# ---------------------------------------------------------------- scoring rules
CONVENTIONAL_RE = re.compile(r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([^)]*\))?(!)?: .+")
TYPE_PTS = {"perf": 4, "feat": 3, "fix": 2, "refactor": 2, "test": 2, "docs": 1,
            "build": 1, "ci": 1, "revert": 1, "style": 0, "chore": 0}
JUNK_RE = re.compile(r"^(wip|update[sd]?|fix(es|ed)?|changes?|stuff|asdf+|minor|temp|tmp|test|\.+|-+|commit|save|done|final)\W*$", re.I)
ISSUE_RE = re.compile(r"\b(close[sd]?|fix(e[sd])?|resolve[sd]?)\s+#\d+", re.I)
TEST_RE = re.compile(r"(^|/)(tests?/|test_[^/]*$)|_test\.\w+$|\.test\.\w+$|\.spec\.\w+$")
CODE_EXT = {".py", ".ipynb", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c",
            ".cpp", ".h", ".cu", ".sql", ".sh", ".kt", ".swift", ".rb", ".php"}
ML_RULES = [  # regex over ADDED lines, points, label
    (re.compile(r"class\s+\w+\(\s*(nn\.)?Module\s*\)"), 3, "new nn.Module"),
    (re.compile(r"\.backward\(\)|optimizer\.step\(\)|\.zero_grad\("), 2, "training loop"),
    (re.compile(r"^\s*(import torch|from torch|import jax|from transformers|import tensorflow|from peft)", re.M), 1, "ML stack"),
]


def _h(sha):
    return hashlib.sha256(sha.encode()).digest()


def analyze(commit, luck=0.05):
    """
    commit = {sha, message (full), files: [paths], added: int, deleted: int, added_text: str}
    Returns score breakdown, rarity, loot item, tags and base XP.
    """
    msg = (commit.get("message") or "").strip()
    lines = msg.splitlines()
    subject = lines[0].strip() if lines else ""
    body = [l for l in lines[1:] if l.strip() and not l.lower().startswith(("signed-off-by", "co-authored-by"))]
    files = commit.get("files", [])
    added, deleted = commit.get("added", 0), commit.get("deleted", 0)
    text = commit.get("added_text", "")

    signals, tags = [], set()

    def add(label, pts):
        signals.append((label, pts))

    m = CONVENTIONAL_RE.match(subject)
    ctype = m.group(1) if m else None
    if ctype:
        tags.add("conventional")
        tags.add(ctype)
        if TYPE_PTS[ctype]:
            add(ctype, TYPE_PTS[ctype])

    if JUNK_RE.match(subject) or len(subject) < 6:
        add("lazy message", -2)
    elif 10 <= len(subject) <= 72:
        add("clean subject", 1)
    if body:
        add("explains why", 1)
    if ISSUE_RE.search(msg):
        add("closes issue", 1)

    tests = [f for f in files if TEST_RE.search(f)]
    code = [f for f in files if PurePosixPath(f).suffix.lower() in CODE_EXT and f not in tests]
    if tests and code:
        add("code + tests", 3)
        tags.add("tested")
    elif tests:
        add("tests", 2)
        tags.add("tested")

    changed = added + deleted
    if changed < 3:
        add("tiny", -2)
    elif changed > 1500:
        add("giant dump", -2)
    elif 20 <= changed <= 400:
        add("focused size", 2)

    if deleted - added >= 30:
        add("net deletion", 2)
        tags.add("refactor")

    ml_pts, ml_labels = 0, []
    for rx, pts, label in ML_RULES:
        if rx.search(text):
            ml_pts += pts
            ml_labels.append(label)
    if ml_pts:
        add(" + ".join(ml_labels), min(ml_pts, 4))
        tags.add("ml")

    if len(re.findall(r'"""|->\s*\w', text)) >= 2:
        add("typed/documented", 1)
    if any(PurePosixPath(f).suffix.lower() == ".md" or f.startswith("docs/") for f in files):
        tags.add("docs")

    score = sum(p for _, p in signals)
    rarity = "Common"
    for need, name in THRESHOLDS:
        if score >= need:
            rarity = name
            break
    if rarity == "Mythic" and "code + tests" not in [l for l, _ in signals]:
        rarity = "Legendary"  # Mythic must ship tests with code

    h = _h(commit["sha"])
    lucky = False
    r = int.from_bytes(h[:4], "big") / 0xFFFFFFFF
    idx = RARITY_ORDER.index(rarity)
    if score > 0 and r < luck and idx < RARITY_ORDER.index("Legendary"):
        rarity = RARITY_ORDER[idx + 1]
        lucky = True

    items = LOOT[rarity]
    item = items[int.from_bytes(h[4:8], "big") % len(items)]
    top = sorted([s for s in signals if s[1] > 0], key=lambda s: -s[1])[:3]
    return {"score": score, "signals": signals, "reason": ", ".join(l for l, _ in top) or "—",
            "tags": sorted(tags), "rarity": rarity, "item": item, "lucky": lucky,
            "base_xp": RARITY_XP[rarity]}


# ---------------------------------------------------------------- levels
MAX_LEVEL = 100


def xp_for_level(level):
    return sum(100 + 25 * (n - 1) for n in range(1, level))


def level_info(xp):
    """-> (level, prestige, xp_into_level, xp_needed_for_next)"""
    cap = xp_for_level(MAX_LEVEL) + 1000
    prestige, xp = divmod(xp, cap)
    level = 1
    while level < MAX_LEVEL and xp >= xp_for_level(level + 1):
        level += 1
    nxt = xp_for_level(level + 1) if level < MAX_LEVEL else cap
    return level, prestige, xp - xp_for_level(level), nxt - xp_for_level(level)


# ---------------------------------------------------------------- perks
SLOT_HEX = {"red": "#f87171", "green": "#4ade80", "blue": "#60a5fa"}
SLOTS = ["red", "green", "blue"]


def _tag(state, t):
    return state.get("tags", {}).get(t, 0)


def _epic_plus(state):
    rc = state.get("rarity_count", {})
    return sum(rc.get(r, 0) for r in ("Epic", "Legendary", "Mythic"))


PERKS = {
    # id: slot, name, how to unlock, unlock check, effect text
    "test_pilot": ("red", "Test Pilot", "10 commits with tests",
                   lambda s: _tag(s, "tested") >= 10, "+25% XP on tested commits"),
    "bug_hunter": ("red", "Bug Hunter", "15 fix: commits",
                   lambda s: _tag(s, "fix") >= 15, "+40% XP on fix: commits"),
    "clean_hands": ("red", "Clean Hands", "20 conventional commits",
                    lambda s: _tag(s, "conventional") >= 20, "+15% XP on conventional commits"),
    "torchbearer": ("green", "Torchbearer", "10 ML commits",
                    lambda s: _tag(s, "ml") >= 10, "+30% XP on ML commits"),
    "janitor": ("green", "Janitor", "5 net-deletion refactors",
                lambda s: _tag(s, "refactor") >= 5, "+50% XP on refactors"),
    "scholar": ("green", "Scholar", "10 commits touching docs",
                lambda s: _tag(s, "docs") >= 10, "+50% XP on docs commits"),
    "lucky_charm": ("blue", "Lucky Charm", "Reach level 10",
                    lambda s: level_info(s.get("xp", 0))[0] >= 10 or level_info(s.get("xp", 0))[1] > 0,
                    "Lucky-drop chance 5% → 12%"),
    "architect": ("blue", "Architect", "3 Epic-or-better drops",
                  lambda s: _epic_plus(s) >= 3, "Epic+ drops give 2× XP"),
    "momentum": ("blue", "Momentum", "Best streak of 14 days",
                 lambda s: s.get("best_streak", 0) >= 14, "+2% XP per streak day (max +20%)"),
}


def check_unlocks(state, stamp):
    """Unlock any newly earned perks. Returns list of perk ids just unlocked."""
    got = state.setdefault("perks", {})
    new = []
    for pid, (_, _, _, check, _) in PERKS.items():
        if pid not in got and check(state):
            got[pid] = stamp
            new.append(pid)
    return new


def resolve_loadout(state, loadout=None):
    """loadout = {"red": "test_pilot", ...} (ids or names). Falls back to first unlocked per slot."""
    loadout = loadout or {}
    unlocked = state.get("perks", {})
    by_name = {v[1].lower(): k for k, v in PERKS.items()}
    equipped = {}
    for slot in SLOTS:
        want = str(loadout.get(slot, "")).strip()
        want = by_name.get(want.lower(), want)
        if want in unlocked and PERKS.get(want, ("",))[0] == slot:
            equipped[slot] = want
        else:
            equipped[slot] = next((p for p in PERKS if PERKS[p][0] == slot and p in unlocked), None)
    return equipped


def luck_for(equipped):
    return 0.12 if "lucky_charm" in equipped.values() else 0.05


def apply_perks(result, equipped, streak):
    """Returns (final_xp, [perk names that fired])."""
    tags = set(result["tags"])
    bonus, fired = 0.0, []
    on = set(p for p in equipped.values() if p)
    rules = [("test_pilot", "tested" in tags, 0.25), ("bug_hunter", "fix" in tags, 0.40),
             ("clean_hands", "conventional" in tags, 0.15), ("torchbearer", "ml" in tags, 0.30),
             ("janitor", "refactor" in tags, 0.50), ("scholar", "docs" in tags, 0.50),
             ("momentum", streak > 0, min(0.02 * streak, 0.20))]
    for pid, cond, b in rules:
        if pid in on and cond and b > 0:
            bonus += b
            fired.append(PERKS[pid][1])
    xp = result["base_xp"] * (1 + bonus)
    if "architect" in on and result["rarity"] in ("Epic", "Legendary", "Mythic"):
        xp *= 2
        fired.append("Architect")
    if "lucky_charm" in on and result["lucky"]:
        fired.append("Lucky Charm")
    return int(round(xp)), fired


def record(state, result):
    """Update the counters perks depend on."""
    tags = state.setdefault("tags", {})
    for t in result["tags"]:
        tags[t] = tags.get(t, 0) + 1
    rc = state.setdefault("rarity_count", {})
    rc[result["rarity"]] = rc.get(result["rarity"], 0) + 1
    coll = state.setdefault("collection", {})
    coll[result["item"]] = coll.get(result["item"], 0) + 1
