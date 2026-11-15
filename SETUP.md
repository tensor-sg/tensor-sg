# Commit Grind — GitHub-only setup (no server)

1. Open your profile repo: github.com/<your-username>/<your-username>
2. Copy `.github/` and `grind/` (grind.py, grind_core.py, loadout.json) into it.
3. Put these two lines in README.md where you want the card:

       <!-- GRIND:START -->
       <!-- GRIND:END -->

4. Settings → Actions → General → Workflow permissions → "Read and write permissions".
5. Actions tab → "Commit Grind" → Run workflow. After that it updates every 6 hours.

Optional, so private-repo commits count: create a fine-grained PAT with read-only
"Contents" + "Metadata" on your repos and save it as a repo secret named GRIND_TOKEN.

## How rarity is earned (not random)

| Signal | Points |
|---|---|
| Conventional type: perf 4 · feat 3 · fix/refactor/test 2 · docs/build/ci 1 | +1..4 |
| Clean subject (10–72 chars) | +1 |
| Body explaining why | +1 |
| "Closes #12" style issue link | +1 |
| Code + tests in the same commit (tests only: +2) | +3 |
| Focused size (20–400 lines changed) | +2 |
| Net deletion of 30+ lines | +2 |
| ML signals: new nn.Module +3, training loop +2, ML imports +1 (max 4) | +1..4 |
| Type hints / docstrings added | +1 |
| Lazy message ("wip", "update", "fix") | −2 |
| Tiny (<3 lines) or giant dump (>1500 lines) | −2 |

Common <2 · Uncommon 2+ · Rare 4+ · Epic 7+ · Legendary 10+ · **Mythic 13+ AND code shipped with tests**.
A lucky drop (5%, or 12% with Lucky Charm) can bump a scoring commit up one tier, but never into Mythic.

## Perks & loadout

Edit `grind/loadout.json` to equip one unlocked perk per slot (names or ids).
The README's "Perks" section lists what's unlocked. A locked or wrong-slot choice
falls back to your first unlocked perk in that slot.

- red: Test Pilot · Bug Hunter · Clean Hands
- green: Torchbearer · Janitor · Scholar
- blue: Lucky Charm · Architect · Momentum

## Notes
- Up to 150 commits are scored per run (each needs one API call for its diff);
  the rest are picked up by the next runs.
- Commit search only sees default branches and commits authored with an email
  linked to your GitHub account.
- Streak days use Asia/Kolkata; change GRIND_TZ in the workflow if needed.
