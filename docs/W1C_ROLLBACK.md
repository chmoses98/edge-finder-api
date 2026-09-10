# W1-C rollback — restoring pre-identity behaviour without touching history

W1-C changes which contract production believes it is looking at, so it needs a
way back that does not depend on anyone reasoning correctly under pressure.

## The shape of the rollback: revert the commit, nothing else

**There is no runtime identity toggle, and that is deliberate.** A flag that
selected between "canonical identity" and "team-pair key" would leave two
competing identity authorities alive at once — which is precisely the condition
that produced CR-3, and which W1-D exists to remove elsewhere. So:

```
git revert -m 1 <W1-C merge commit>
```

| changed | rollback effect |
|---|---|
| `lib/edgelab/market_identity.py` (new) | unused after revert |
| `scripts/build_kalshi_registry.py` | returns to `registry[kalshi_key] = entry`, i.e. the second doubleheader leg silently overwrites the first again |
| `scripts/merge_odds.py` | `find_registry_entry` returns to iterating a `set` and taking the first hit, with no collision awareness |
| `docs/W1C_MARKET_IDENTITY_TRACE.md` | documentation only |
| `tests/test_w1c_market_identity.py` | the forward-looking CR-3 guards stop running |

## Why no historical evidence needs repairing

W1-C writes nothing into `data/`. It changes how the NEXT run resolves
identity, not what any past run recorded:

* No wager, settlement, recommendation, CLV or registry snapshot is modified.
* The contaminated 2026-06-17 and 2026-07-11 slates are read **read-only** by
  the tests and are left exactly as they are. They are the evidence.
* `data/kalshi_market_registry.json` is regenerated from scratch on every
  `fetch-slate.yml` run, so the first post-revert run restores the old shape
  with no repair step.

Rollback is a code revert plus one ordinary scheduled run.

## What a revert costs, stated plainly

Reverting restores the defect. A doubleheader would again give both legs the
same `kalshiKey`, the registry would again let one leg overwrite the other, and
the slate join would again hand both physical games the same markets. On
2026-07-11 that put `KXMLBTEAMTOTAL-26JUL111605MILPIT-MIL4` on two different
baseball games; on 2026-06-17 it reached real-money-tier rows. Rollback is a
safety valve, not a neutral choice.

## One authority, and the aliases that remain derived

`kalshiKey` is **not** deleted — research, display and index code still read it.
After W1-C it is explicitly a *lookup convenience*, never authoritative
physical-game identity:

* the authoritative anchor is the **MLB gamePk**
  (`market_identity.physical_game_key`);
* `registry_key()` will return a gamePk-based or full-event-suffix key and
  **never** a bare team pair;
* where a team pair names more than one event on a date, the registry records
  the collision and the money-path join **refuses** rather than choosing.

So there is one authority (gamePk), and the remaining `kalshiKey` uses are
derived and read-only with respect to identity decisions.

## If something is wrong but a full revert is too blunt

The failure mode W1-C introduces is *over-refusal* — a doubleheader losing its
Kalshi markets because the legs could not be told apart. That is the intended
direction. If a specific matchup refuses for a reason that turns out to be a
plumbing bug rather than genuine ambiguity, the narrow fix is to supply the
missing leg evidence (start time, leg number, gamePk) for that path, **not** to
reinstate the team-pair key. Every collision is recorded in the registry's
`registry_key_collisions`, so the diagnosis starts from data rather than a guess.

Reinstating team-pair identity "temporarily" is not a rollback option. It is
the original defect with a smaller blast radius.
