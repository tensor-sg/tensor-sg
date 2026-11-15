#!/usr/bin/env python3
"""
Commit Grind — GitHub-only edition. Runs inside GitHub Actions, no server.

Each run:
  1. finds your commits (GitHub Search API), then reads each new commit's diff
  2. scores it with grind_core: type, message quality, tests next to code,
     focused size, refactors, ML signals -> rarity (Common ... Mythic) + loot
  3. applies your equipped perks (grind/loadout.json) -> XP, levels, streak
  4. writes grind-card.svg + grind-state.json, refreshes the README block
     between <!-- GRIND:START --> and <!-- GRIND:END -->

Env: GH_USER, GH_TOKEN, GRIND_TZ (default Asia/Kolkata), GRIND_MOCK (test data path)
"""
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import grind_core as core  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATE_FILE = ROOT / "grind-state.json"
CARD_FILE = ROOT / "grind-card.svg"
README = ROOT / "README.md"
LOADOUT_FILE = HERE / "loadout.json"

USER = os.environ.get("GH_USER", "")
TOKEN = os.environ.get("GH_TOKEN", "")
TZ = ZoneInfo(os.environ.get("GRIND_TZ", "Asia/Kolkata"))
MOCK = os.environ.get("GRIND_MOCK")
DETAIL_LIMIT = 150  # commit diffs fetched per run; the rest are picked up next run

CAMO_TIERS = [("Bronze", 10, "#cd7f32"), ("Silver", 25, "#c0c0c0"), ("Gold", 50, "#facc15"),
              ("Platinum", 100, "#e5e4e2"), ("Diamond", 200, "#67e8f9"), ("Obsidian", 500, "#a855f7")]
BADGES = {
    "first_blood": ("🩸", "First Blood", "First tracked commit"),
    "first_rare": ("💎", "Quality Control", "Earn a Rare commit"),
    "first_epic": ("🔮", "Epic Moment", "Earn an Epic commit"),
    "legendary": ("✨", "Touched by Gold", "Earn a Legendary commit"),
    "mythic": ("👁️", "Six Eyes", "Earn a Mythic commit (score 13+ with tests)"),
    "lucky": ("🍀", "Lucky Break", "Get a lucky tier bump"),
    "streak_3": ("🔥", "Warming Up", "3-day streak"),
    "streak_7": ("⚡", "Locked In", "7-day streak"),
    "streak_30": ("🌀", "Domain Expansion", "30-day streak"),
    "well_rested": ("😴", "Well Rested", "Come back after a day off"),
    "early_bird": ("🌅", "Early Bird", "Commit between 5 and 8 AM"),
    "polyglot": ("🌐", "Polyglot", "Commits in 3+ languages"),
    "repos_5": ("🧭", "World Traveller", "Commit to 5 repos"),
    "commits_100": ("💯", "Centurion", "100 commits"),
    "collector_10": ("🎒", "Hoarder", "10 unique loot items"),
    "full_set": ("👑", "Completionist", "Every loot item"),
    "full_loadout": ("🎽", "Fully Kitted", "Unlock a perk in all 3 slots"),
    "gold_camo": ("🥇", "Gold Plated", "Unlock a Gold camo"),
    "prestige": ("⭐", "Prestige", "Pass level 100"),
}


# ---------------------------------------------------------------- github
def api(url):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "commit-grind",
        **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}),
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def search_commits():
    out = []
    q = urllib.parse.quote(f"author:{USER}")
    for page in range(1, 11):  # search caps at 1000 results
        items = api(f"https://api.github.com/search/commits?q={q}&sort=author-date"
                    f"&order=desc&per_page=100&page={page}").get("items", [])
        out += [{"sha": it["sha"], "date": it["commit"]["author"]["date"],
                 "repo": it["repository"]["full_name"]} for it in items]
        if len(items) < 100:
            break
    return out


def commit_detail(repo, sha):
    d = api(f"https://api.github.com/repos/{repo}/commits/{sha}")
    files = d.get("files", [])
    added_text = "\n".join(
        line[1:] for f in files for line in (f.get("patch") or "").splitlines()
        if line.startswith("+") and not line.startswith("+++"))
    return {"sha": sha, "message": d["commit"]["message"],
            "files": [f["filename"] for f in files],
            "added": d.get("stats", {}).get("additions", 0),
            "deleted": d.get("stats", {}).get("deletions", 0),
            "added_text": added_text}


def repo_language(repo, cache):
    if repo not in cache:
        try:
            cache[repo] = api(f"https://api.github.com/repos/{repo}").get("language")
        except Exception:
            cache[repo] = None
    return cache[repo]


# ---------------------------------------------------------------- state
def load_state():
    base = {"xp": 0, "commits": 0, "seen": [], "days": [], "repos": [], "collection": {},
            "camos": {}, "badges": {}, "perks": {}, "tags": {}, "rarity_count": {},
            "drops": [], "lang_cache": {}, "streak": 0, "best_streak": 0, "freezes": 1}
    if STATE_FILE.exists():
        base.update(json.loads(STATE_FILE.read_text()))
    return base


def streaks(days, today):
    """Walk active days with freezes (start 1, earn 1 per 7-day run, max 3)."""
    ds = sorted(dt.date.fromisoformat(d) for d in set(days))
    if not ds:
        return 0, 0, 1
    streak = best = 0
    freezes, prev = 1, None
    for d in ds:
        gap = 0 if prev is None else (d - prev).days - 1
        if prev is None or gap == 0:
            streak += 1
        elif gap <= freezes:
            freezes -= gap
            streak += 1
        else:
            streak = 1
        if streak % 7 == 0 and freezes < 3:
            freezes += 1
        best, prev = max(best, streak), d
    if (today - ds[-1]).days - 1 > freezes:
        return 0, best, freezes
    return streak, best, freezes


def camo(count):
    tier = None
    for name, need, color in CAMO_TIERS:
        if count >= need:
            tier = (name, color)
    return tier


def load_loadout():
    if LOADOUT_FILE.exists():
        try:
            return json.loads(LOADOUT_FILE.read_text())
        except json.JSONDecodeError:
            print("loadout.json is not valid JSON, using auto-loadout")
    return {}


# ---------------------------------------------------------------- core loop
def process(state, found, get_detail, get_lang):
    seen = set(state["seen"])
    todo = sorted([c for c in found if c["sha"][:12] not in seen], key=lambda c: c["date"])
    todo = todo[:DETAIL_LIMIT]
    loadout = load_loadout()
    today = dt.datetime.now(TZ).date()

    for c in todo:
        try:
            detail = get_detail(c["repo"], c["sha"])
        except Exception as e:
            print(f"skip {c['sha'][:7]}: {e}")
            continue
        when = dt.datetime.fromisoformat(c["date"].replace("Z", "+00:00")).astimezone(TZ)
        stamp = when.date().isoformat()

        state["days"] = sorted(set(state["days"] + [stamp]))
        state["streak"], state["best_streak"], state["freezes"] = streaks(state["days"], when.date())

        equipped = core.resolve_loadout(state, loadout)
        result = core.analyze(detail, luck=core.luck_for(equipped))
        xp, fired = core.apply_perks(result, equipped, state["streak"])

        core.record(state, result)
        state["xp"] += xp
        state["commits"] += 1
        state["seen"].append(c["sha"][:12])
        if c["repo"] not in state["repos"]:
            state["repos"].append(c["repo"])
        lang = get_lang(c["repo"])
        if lang:
            state["camos"][lang] = state["camos"].get(lang, 0) + 1
        state["drops"] = (state["drops"] + [{
            "item": result["item"], "rarity": result["rarity"], "reason": result["reason"],
            "lucky": result["lucky"], "xp": xp, "perks": fired,
            "repo": c["repo"].split("/")[-1], "msg": detail["message"].splitlines()[0][:60] if detail["message"] else ""}])[-5:]

        def unlock(k):
            state["badges"].setdefault(k, stamp)
        unlock("first_blood")
        r = result["rarity"]
        rank = core.RARITY_ORDER.index(r)
        if rank >= 2: unlock("first_rare")
        if rank >= 3: unlock("first_epic")
        if rank >= 4: unlock("legendary")
        if r == "Mythic": unlock("mythic")
        if result["lucky"]: unlock("lucky")
        if 5 <= when.hour < 8: unlock("early_bird")
        for pid in core.check_unlocks(state, stamp):
            print(f"PERK UNLOCKED: {core.PERKS[pid][1]}")

    # whole-history badges
    def unlock(k):
        state["badges"].setdefault(k, today.isoformat())
    state["streak"], state["best_streak"], state["freezes"] = streaks(state["days"], today)
    for n, k in ((3, "streak_3"), (7, "streak_7"), (30, "streak_30")):
        if state["best_streak"] >= n: unlock(k)
    ds = [dt.date.fromisoformat(d) for d in state["days"]]
    if any((b - a).days >= 2 for a, b in zip(ds, ds[1:])): unlock("well_rested")
    if len(state["camos"]) >= 3: unlock("polyglot")
    if len(state["repos"]) >= 5: unlock("repos_5")
    if state["commits"] >= 100: unlock("commits_100")
    if len(state["collection"]) >= 10: unlock("collector_10")
    if len(state["collection"]) >= core.TOTAL_ITEMS: unlock("full_set")
    if all(any(core.PERKS[p][0] == s for p in state["perks"]) for s in core.SLOTS): unlock("full_loadout")
    if any((camo(n) or ("",))[0] in ("Gold", "Platinum", "Diamond", "Obsidian") for n in state["camos"].values()):
        unlock("gold_camo")
    if core.level_info(state["xp"])[1] > 0: unlock("prestige")
    core.check_unlocks(state, today.isoformat())
    state["equipped"] = core.resolve_loadout(state, loadout)
    return len(todo), len([c for c in found if c["sha"][:12] not in set(state["seen"])])


# ---------------------------------------------------------------- render
def esc(s):
    return html.escape(str(s), quote=True)


def card(state):
    level, prestige, cur, need = core.level_info(state["xp"])
    W, H, bar_w = 560, 410, 340
    fill = max(4, int(bar_w * cur / need)) if need else bar_w
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
         f'font-family="Segoe UI, Ubuntu, Helvetica, Arial, sans-serif">',
         '<defs><linearGradient id="xp" x1="0" x2="1"><stop offset="0" stop-color="#22d3ee"/>'
         '<stop offset="1" stop-color="#a855f7"/></linearGradient></defs>',
         f'<rect x="0.5" y="0.5" width="{W-1}" height="{H-1}" rx="12" fill="#0d1117" stroke="#30363d"/>',
         f'<text x="20" y="34" fill="#e6edf3" font-size="18" font-weight="700">{esc(USER or "player")} · Commit Grind</text>',
         f'<text x="{W-20}" y="34" fill="#facc15" font-size="16" font-weight="700" text-anchor="end">'
         f'Lv {level}{" · P" + str(prestige) if prestige else ""}</text>',
         f'<rect x="20" y="48" width="{bar_w}" height="10" rx="5" fill="#21262d"/>',
         f'<rect x="20" y="48" width="{fill}" height="10" rx="5" fill="url(#xp)"/>',
         f'<text x="{30 + bar_w}" y="58" fill="#8b949e" font-size="12">{cur} / {need} XP</text>']

    rc = state.get("rarity_count", {})
    stats = [("STREAK", f'{state["streak"]}d'), ("FREEZES", state["freezes"]), ("COMMITS", state["commits"]),
             ("EPIC+", sum(rc.get(r, 0) for r in ("Epic", "Legendary", "Mythic"))),
             ("LOOT", f'{len(state["collection"])}/{core.TOTAL_ITEMS}')]
    for i, (label, val) in enumerate(stats):
        x = 20 + i * 108
        p.append(f'<text x="{x}" y="86" fill="#8b949e" font-size="11" letter-spacing="1">{label}</text>')
        p.append(f'<text x="{x}" y="106" fill="#e6edf3" font-size="16" font-weight="700">{esc(val)}</text>')

    p.append('<text x="20" y="140" fill="#e6edf3" font-size="13" font-weight="700">Latest drops</text>')
    for i, d in enumerate(reversed(state["drops"])):
        y = 162 + i * 22
        col = core.RARITY_HEX[d["rarity"]]
        p.append(f'<rect x="20" y="{y-12}" width="78" height="16" rx="8" fill="{col}" fill-opacity="0.15" stroke="{col}"/>')
        p.append(f'<text x="59" y="{y}" fill="{col}" font-size="10" font-weight="700" text-anchor="middle">'
                 f'{d["rarity"].upper()}{"🍀" if d.get("lucky") else ""}</text>')
        p.append(f'<text x="108" y="{y}" fill="{col}" font-size="12">{esc(d["item"])}</text>')
        p.append(f'<text x="290" y="{y}" fill="#8b949e" font-size="10">{esc(d["reason"][:44])}</text>')
        p.append(f'<text x="{W-20}" y="{y}" fill="#6e7681" font-size="10" text-anchor="end">+{d.get("xp", 0)}</text>')

    p.append('<text x="20" y="292" fill="#e6edf3" font-size="13" font-weight="700">Loadout</text>')
    for i, slot in enumerate(core.SLOTS):
        pid = state.get("equipped", {}).get(slot)
        x, col = 20 + i * 176, core.SLOT_HEX[slot]
        name = core.PERKS[pid][1] if pid else "locked"
        p.append(f'<rect x="{x}" y="302" width="166" height="22" rx="6" fill="{col}" '
                 f'fill-opacity="{0.15 if pid else 0.04}" stroke="{col}" stroke-opacity="{1 if pid else 0.35}"/>')
        p.append(f'<text x="{x + 83}" y="317" fill="{col if pid else "#6e7681"}" font-size="11" '
                 f'font-weight="700" text-anchor="middle">{esc(name)}</text>')

    top = sorted(state["camos"].items(), key=lambda kv: -kv[1])[:4]
    p.append('<text x="20" y="348" fill="#8b949e" font-size="11" letter-spacing="1">CAMOS</text>')
    for i, (lang, count) in enumerate(top):
        name, color = camo(count) or ("—", "#6e7681")
        x = 80 + i * 120
        p.append(f'<text x="{x}" y="348" fill="#c9d1d9" font-size="11">{esc(lang[:10])} '
                 f'<tspan fill="{color}" font-weight="700">{name}</tspan></text>')

    icons = " ".join(BADGES[k][0] for k in BADGES if k in state["badges"])
    p.append(f'<text x="20" y="378" fill="#8b949e" font-size="11" letter-spacing="1">BADGES {len(state["badges"])}/{len(BADGES)}</text>')
    p.append(f'<text x="130" y="379" font-size="14">{esc(icons) or "—"}</text>')
    p.append("</svg>")
    return "\n".join(p)


def readme_block(state):
    out = ["<!-- GRIND:START -->", '<img src="./grind-card.svg" alt="Commit Grind card" />', "",
           "<details><summary>🎽 Perks</summary>", "",
           "| Slot | Perk | Effect | Unlock |", "|---|---|---|---|"]
    for pid, (slot, name, how, _, effect) in core.PERKS.items():
        mark = "✅" if pid in state["perks"] else "🔒"
        eq = " **(equipped)**" if pid in state.get("equipped", {}).values() else ""
        out.append(f"| {slot} | {mark} {name}{eq} | {effect} | {how} |")
    out += ["", "</details>", "", "<details><summary>🏅 Badges</summary>", ""]
    for k, (icon, title, desc) in BADGES.items():
        out.append(f"- {icon if k in state['badges'] else '🔒'} **{title}** — {desc}")
    out += ["", "</details>", "", "<details><summary>🎒 Loot binder</summary>", ""]
    for r in core.RARITY_ORDER:
        owned = [f"{i} ×{state['collection'][i]}" if state["collection"].get(i, 0) > 1 else i
                 for i in core.LOOT[r] if i in state["collection"]]
        out.append(f"- **{r}** ({len(owned)}/{len(core.LOOT[r])}): " + (", ".join(owned) or "—"))
    out += ["", "</details>", "<!-- GRIND:END -->"]
    return "\n".join(out)


def main():
    state = load_state()
    if MOCK:
        data = json.loads(Path(MOCK).read_text())
        found = data["commits"]
        details = {c["sha"]: c for c in data["commits"]}
        get_detail = lambda repo, sha: details[sha]  # noqa: E731
        get_lang = lambda repo: data["languages"].get(repo)  # noqa: E731
    else:
        if not USER:
            sys.exit("GH_USER is not set")
        found = search_commits()
        get_detail = commit_detail
        get_lang = lambda repo: repo_language(repo, state["lang_cache"])  # noqa: E731
    done, left = process(state, found, get_detail, get_lang)

    STATE_FILE.write_text(json.dumps(state, indent=1))
    CARD_FILE.write_text(card(state))
    if README.exists():
        text = README.read_text()
        if "<!-- GRIND:START -->" in text and "<!-- GRIND:END -->" in text:
            README.write_text(re.sub(r"<!-- GRIND:START -->.*?<!-- GRIND:END -->",
                                     lambda _: readme_block(state), text, flags=re.S))
    lvl = core.level_info(state["xp"])[0]
    print(f"Processed {done} commit(s), {left} still queued. Level {lvl}, "
          f"{state['commits']} commits, streak {state['streak']}.")


if __name__ == "__main__":
    main()
