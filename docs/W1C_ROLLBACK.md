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
| `scripts/build_kalshi_registry.py` | the authoritative store returns to being keyed by the team pair, so a doubleheader's second leg is dropped again — and the `events` map, with every leg's full market payload, stops being written |
| `scripts/merge_odds.py` | `find_registry_entry` returns to iterating a `set` and taking the first hit, with no collision awareness; `kalshiEventTickerSuffix` stops being stamped, so nothing downstream can bind a contract to a game |
| `scripts/build_market_ledger.py` | `accepted_row` stops requiring proven identity, so a row becomes actionable again on price alone; the row loses `marketFamily`, `marketHorizon`, `selection`, `direction`, `threshold`, `contractSide`, `physicalGameKey` and `identityStatus`; `contract_ticker_for` goes away, so the merged block and the priced book may again name different contracts; and the run line is again handed to both teams |
| `scripts/audit/w1c_identity_blast_radius.py` (new) | measurement tool only |
| `scripts/audit/w1c_live_identity_evidence.py` (new) | measurement tool only |
| `.github/workflows/w1c-live-identity-rehearsal.yml` (new) | the live read-only rehearsal stops running |
| `docs/W1C_MARKET_IDENTITY_TRACE.md` | documentation only |
| `tests/test_w1c_market_identity.py` | the forward-looking CR-3 guards stop running |

Two test sandboxes (`tests/test_end_to_end_pipeline_sandbox.py`,
`tests/test_build_market_ledger_projection_boundary.py`) gain
`market_identity.py` and `kalshi_ticker_time.py` in their copy manifests. A
revert removes the imports that need them, so the manifests revert cleanly
with everything else; leaving the extra entries in place would also be
harmless.

### The registry document gains a field; it loses none

`data/kalshi_market_registry.json` now carries `events` (authoritative, keyed by
Kalshi event suffix) **alongside** the existing `registry` map, which is now a
derived team-pair compatibility index. Every existing reader that looks a game
up by `f"{away}{home}"` keeps working on an ordinary single-game slate.

The one deliberate behaviour change for those readers: on a **doubleheader**
date the team pair gets no compat entry at all, because it names two events and
therefore names no game. A reader that finds nothing there must resolve through
`events` with real physical evidence, or refuse. That is the intended direction
— the alternative is an entry pointing at one leg, which is a wrong answer that
reads like a right one.

A revert removes `events` and restores the old shape. `capture_closing_lines.py`
and any other `registry`-keyed reader are unaffected either way, since
`registry` never stops being written.

### What downstream consumers see after a revert

The identity fields are ADDITIVE on the ledger row — nothing reads them as a
precondition today, so a revert removes columns rather than breaking a
contract. `marketTicker`, `ticker`, `seriesTicker` and `line` predate W1-C and
survive a revert unchanged, which is what settlement and CLV joins actually
use.

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
