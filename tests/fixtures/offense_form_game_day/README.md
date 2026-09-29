Deterministic game-day inputs for
`tests/test_team_offense_form.py::test_enrich_data_offense_baseline_is_identical_with_and_without_form_context`.

That test protects a CODE invariant -- adding `data/team_offense_form.json`
must not change any projection-feeding field `scripts/enrich_data.py` writes --
and used to borrow the live committed `data/` files as its fixture. On
2026-09-28 (an MLB off-day) the live slate held zero games, so the invariant
had nothing to act on and the test failed for a reason unrelated to the code.

`slate.json` is two synthetic games (MIL@PIT, NYY@BOS). The other four files
are the committed `data/` team-level inputs of 2026-09-29, trimmed to those
four teams. Nothing here is read by production.
