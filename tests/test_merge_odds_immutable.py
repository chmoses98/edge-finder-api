#!/usr/bin/env python3
"""
tests/test_merge_odds_immutable.py
=====================================
Golden-equivalence regression suite for scripts/merge_odds.py's Phase 4
immutable-transform conversion (see docs/IMMUTABLE_PIPELINE.md).

Written and run against the ORIGINAL top-level-mutation implementation
FIRST to establish a golden baseline, then re-run UNCHANGED after the
refactor to prove identical output. Each test runs the real script as a
subprocess with cwd redirected to an isolated tmp directory (the same
pattern already established in tests/test_rfi_fallback.py's
TestMergeOddsRFIIntegration) — never touches the real repository's data/.

Key finding from the pre-refactor audit that these tests specifically
guard against: api/odds.js already independently populates
data/odds.json's games[].books.kalshi with its own kalshi-native fields
(ml, f5ml, nrfi, teamTotals, total) BEFORE merge_odds.py ever runs.
merge_odds.py's `kalshi_books = game['odds'].setdefault('kalshi', {})`
starts from that pre-existing dict and only overwrites specific keys —
it does not replace it. Because the registry-sourced key names are
different (nrfi_yrfi vs nrfi, team_totals vs teamTotals), both the
odds.js-native and merge_odds.py-registry versions can coexist as
separate keys in the same dict. A refactor that rebuilds kalshi_books
from an empty dict instead of a copy of what was already there would
silently drop this pre-existing data — exactly the kind of regression
this suite is designed to catch.
"""

import json
import os
import subprocess
import sys
import tempfile
import shutil

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")


class MergeOddsHarness:
    """Shared fixture-building + execution helper, subprocess+tmpdir isolated."""

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.data_dir = os.path.join(self.tmp, "data")
        os.makedirs(self.data_dir)

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, filename, data):
        with open(os.path.join(self.data_dir, filename), "w") as f:
            json.dump(data, f)

    def _read_slate(self):
        with open(os.path.join(self.data_dir, "slate.json")) as f:
            return json.load(f)

    def _run(self):
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "merge_odds.py")],
            capture_output=True, text=True, cwd=self.tmp,
        )
        return result

    # ── Fixture builders ──────────────────────────────────────────────────

    def make_slate(self, games):
        return {"date": "2026-07-27", "games": games}

    def make_game(self, away_abbr="NYY", home_abbr="PHI", away_team="New York Yankees",
                  home_team="Philadelphia Phillies", status="Scheduled", extra=None):
        g = {
            "away": {"abbr": away_abbr, "team": away_team},
            "home": {"abbr": home_abbr, "team": home_team},
            "status": status,
        }
        if extra:
            g.update(extra)
        return g

    def make_odds_entry(self, away_team="New York Yankees", home_team="Philadelphia Phillies",
                         books=None, pinnacle_vf=None, event_id="evt1", commence_time="2026-07-27T23:05:00Z"):
        return {
            "awayTeam": away_team, "homeTeam": home_team,
            "books": books if books is not None else {},
            "pinnacleVF": pinnacle_vf,
            "pinnacleF5VF": None,
            "eventId": event_id,
            "commenceTime": commence_time,
        }

    def make_odds(self, entries):
        return {"games": entries}

    def make_registry(self, entries):
        return {"registry": entries}

    def make_ml_market(self, away_am=-110, home_am=-110):
        return {
            "prices": {
                "away": {"american": away_am}, "home": {"american": home_am},
            },
            "away_ticker": "KXMLBGAME-TEST-AWAY", "home_ticker": "KXMLBGAME-TEST-HOME",
        }


class TestCompleteOddsData(MergeOddsHarness):

    def test_full_market_coverage_all_types_populated(self):
        registry = self.make_registry({
            "NYYPHI": {
                "kalshi_key": "NYYPHI", "game_time_et": "7:05 PM",
                "markets": {
                    "moneyline": self.make_ml_market(-115, -105),
                    "spread": {"best_line": {"ticker": "t1", "team": "NYY", "win_by_over": 1.5,
                                              "implied_pct": 52.0, "american": -108}, "lines": []},
                    "total": {"best_line": {"ticker": "t2", "total": 8.5, "implied_pct": 51.0,
                                             "american": -104}, "lines": []},
                    "team_total_away": {"team": "NYY", "best_line": {"ticker": "t3", "over_n": 4,
                                        "implied_pct": 53.0, "american": -113}, "lines": []},
                    "team_total_home": {"team": "PHI", "best_line": {"ticker": "t4", "over_n": 4,
                                        "implied_pct": 49.0, "american": 104}, "lines": []},
                    "f5_moneyline": {"prices": {"away": {"american": -120}, "home": {"american": 100},
                                                 "tie": {"american": 450}},
                                      "away_ticker": "KXMLBF5-TEST-AWAY", "home_ticker": "KXMLBF5-TEST-HOME"},
                    "f5_spread": {"best_line": {"ticker": "t5", "team": "NYY", "win_by_over": 0.5,
                                  "implied_pct": 55.0, "american": -122}, "lines": []},
                    "f5_total": {"best_line": {"ticker": "t6", "total": 4.5, "implied_pct": 50.0,
                                 "american": -100}, "lines": []},
                    "rfi": {"ticker": "KXMLBRFI-TEST", "prices": {
                        "yrfi": {"american": -135, "implied_pct": 57.0},
                        "nrfi": {"american": 115, "implied_pct": 43.0},
                    }},
                },
            }
        })
        self._write("slate.json", self.make_slate([self.make_game()]))
        self._write("odds.json", self.make_odds([self.make_odds_entry(pinnacle_vf={"away": 48.0, "home": 52.0})]))
        self._write("kalshi_market_registry.json", registry)

        result = self._run()
        assert result.returncode == 0, result.stderr
        g = self._read_slate()["games"][0]
        kal = g["odds"]["kalshi"]

        assert kal["ml"]["away"] == -115 and kal["ml"]["home"] == -105
        assert kal["rl"]["best_ticker"] == "t1"
        assert kal["total"]["line"] == 8.5
        assert kal["team_totals"]["away"]["line"] == 4
        assert kal["team_totals"]["home"]["line"] == 4
        assert kal["f5ml"]["away"] == -120 and kal["f5ml"]["home"] == 100
        assert kal["f5_spread"]["best_ticker"] == "t5"
        assert kal["f5_total"]["line"] == 4.5
        assert kal["nrfi_yrfi"]["yrfi_american"] == -135
        assert g["kalshiKey"] == "NYYPHI"
        assert g["kalshiVF"]["away"] is not None
        assert g["kalshiF5VF"]["away"] is not None
        assert g["pinnacleVF"] == {"away": 48.0, "home": 52.0}


class TestMissingBookData(MergeOddsHarness):

    def test_unmatched_game_is_left_completely_unchanged(self):
        game = self.make_game(away_abbr="BOS", home_abbr="TB",
                               away_team="Boston Red Sox", home_team="Tampa Bay Rays",
                               extra={"someUpstreamField": "preserved"})
        self._write("slate.json", self.make_slate([game]))
        # Odds entry for a completely different game.
        self._write("odds.json", self.make_odds([self.make_odds_entry()]))
        self._write("kalshi_market_registry.json", self.make_registry({}))

        result = self._run()
        assert result.returncode == 0, result.stderr
        g = self._read_slate()["games"][0]
        assert "odds" not in g
        assert "kalshiKey" not in g
        assert g["someUpstreamField"] == "preserved"
        assert "unmatched" in result.stdout.lower() or "BOS@TB" in result.stdout


class TestMissingKalshiData(MergeOddsHarness):

    def test_missing_registry_entirely_falls_back_to_kalshi_key_only(self):
        self._write("slate.json", self.make_slate([self.make_game()]))
        self._write("odds.json", self.make_odds([self.make_odds_entry()]))
        # No kalshi_market_registry.json file at all.

        result = self._run()
        assert result.returncode == 0, result.stderr
        g = self._read_slate()["games"][0]
        assert g["kalshiKey"] == "NYYPHI"
        assert g["kalshiVF"] is None

    def test_missing_registry_entry_for_this_game_uses_fallback_branch(self):
        self._write("slate.json", self.make_slate([self.make_game()]))
        self._write("odds.json", self.make_odds([self.make_odds_entry()]))
        self._write("kalshi_market_registry.json", self.make_registry({
            "OTHER_GAME": {"kalshi_key": "OTHER_GAME", "markets": {}},
        }))

        result = self._run()
        assert result.returncode == 0, result.stderr
        g = self._read_slate()["games"][0]
        assert g["kalshiKey"] == "NYYPHI"
        assert g["kalshiVF"] is None


class TestPartialMarketAvailability(MergeOddsHarness):

    def test_only_moneyline_present_other_markets_absent(self):
        registry = self.make_registry({
            "NYYPHI": {"kalshi_key": "NYYPHI", "markets": {"moneyline": self.make_ml_market()}}
        })
        self._write("slate.json", self.make_slate([self.make_game()]))
        self._write("odds.json", self.make_odds([self.make_odds_entry()]))
        self._write("kalshi_market_registry.json", registry)

        result = self._run()
        assert result.returncode == 0, result.stderr
        kal = self._read_slate()["games"][0]["odds"]["kalshi"]
        assert "ml" in kal
        assert "rl" not in kal
        assert "total" not in kal
        assert "f5ml" not in kal
        assert "nrfi_yrfi" not in kal


class TestSentinelAndNullPrices(MergeOddsHarness):
    """
    merge_odds.py performs NO sentinel screening itself (confirmed by
    reading the file — no is_sentinel/SENTINEL_PRICES reference anywhere
    in it). Sentinel values pass straight through unfiltered; screening
    happens downstream in protect_slate.py/lib/sentinel_validator.py.
    """

    def test_sentinel_price_passes_through_unfiltered(self):
        registry = self.make_registry({
            "NYYPHI": {"kalshi_key": "NYYPHI", "markets": {
                "moneyline": self.make_ml_market(away_am=19900, home_am=-110)
            }}
        })
        self._write("slate.json", self.make_slate([self.make_game()]))
        self._write("odds.json", self.make_odds([self.make_odds_entry()]))
        self._write("kalshi_market_registry.json", registry)

        result = self._run()
        assert result.returncode == 0, result.stderr
        kal = self._read_slate()["games"][0]["odds"]["kalshi"]
        assert kal["ml"]["away"] == 19900, (
            "merge_odds.py must not filter sentinel values — that is a downstream concern"
        )

    def test_null_ml_price_produces_no_kalshi_vf(self):
        registry = self.make_registry({
            "NYYPHI": {"kalshi_key": "NYYPHI", "markets": {
                "moneyline": {
                    "prices": {"away": {"american": None}, "home": {"american": -110}},
                    "away_ticker": "t", "home_ticker": "t2",
                }
            }}
        })
        self._write("slate.json", self.make_slate([self.make_game()]))
        self._write("odds.json", self.make_odds([self.make_odds_entry()]))
        self._write("kalshi_market_registry.json", registry)

        result = self._run()
        assert result.returncode == 0, result.stderr
        g = self._read_slate()["games"][0]
        assert g["odds"]["kalshi"]["ml"]["away"] is None
        assert "kalshiVF" not in g, (
            "kalshiVF must only be computed when BOTH away and home american odds are present"
        )


class TestMalformedButPreviouslyTolerated(MergeOddsHarness):

    def test_missing_odds_json_crashes_uncaught_by_design(self):
        """
        Documents actual current behavior: data/odds.json is the ONLY
        input NOT wrapped in try/except (unlike slate.json, which fails
        cleanly with exit code 1). This is an existing asymmetry, not
        something this phase's refactor should silently fix — preserved
        exactly, not corrected, since correcting it would be a behavior
        change outside this phase's scope.
        """
        self._write("slate.json", self.make_slate([self.make_game()]))
        # odds.json intentionally not written.
        result = self._run()
        assert result.returncode != 0

    def test_missing_kalshi_search_json_does_not_crash(self):
        self._write("slate.json", self.make_slate([self.make_game()]))
        self._write("odds.json", self.make_odds([self.make_odds_entry()]))
        self._write("kalshi_market_registry.json", self.make_registry({}))
        result = self._run()
        assert result.returncode == 0, result.stderr

    def test_empty_prices_block_does_not_crash(self):
        registry = self.make_registry({
            "NYYPHI": {"kalshi_key": "NYYPHI", "markets": {"moneyline": {"prices": {}}}}
        })
        self._write("slate.json", self.make_slate([self.make_game()]))
        self._write("odds.json", self.make_odds([self.make_odds_entry()]))
        self._write("kalshi_market_registry.json", registry)
        result = self._run()
        assert result.returncode == 0, result.stderr


class TestPreExistingKalshiBooksPreserved(MergeOddsHarness):
    """
    api/odds.js independently pre-populates books.kalshi with kalshi-native
    fields (ml/f5ml/nrfi/teamTotals/total) before merge_odds.py runs. Since
    merge_odds.py writes DIFFERENTLY-NAMED keys for the registry path
    (nrfi_yrfi vs nrfi, team_totals vs teamTotals), both must coexist —
    merge_odds.py must never wipe out pre-existing books.kalshi content,
    only add/overwrite the specific keys it owns.
    """

    def test_preexisting_native_nrfi_key_survives_alongside_registry_nrfi_yrfi(self):
        books = {"kalshi": {"nrfi": {"nrfi": -120, "yrfi": 100, "source": "kalshi_native"}}}
        registry = self.make_registry({
            "NYYPHI": {"kalshi_key": "NYYPHI", "markets": {"rfi": {
                "ticker": "KXMLBRFI-TEST",
                "prices": {"yrfi": {"american": -135}, "nrfi": {"american": 115}},
            }}}
        })
        self._write("slate.json", self.make_slate([self.make_game()]))
        self._write("odds.json", self.make_odds([self.make_odds_entry(books=books)]))
        self._write("kalshi_market_registry.json", registry)

        result = self._run()
        assert result.returncode == 0, result.stderr
        kal = self._read_slate()["games"][0]["odds"]["kalshi"]
        assert kal["nrfi"] == {"nrfi": -120, "yrfi": 100, "source": "kalshi_native"}, (
            "pre-existing native 'nrfi' key from api/odds.js must survive unchanged"
        )
        assert kal["nrfi_yrfi"]["yrfi_american"] == -135, (
            "registry-sourced 'nrfi_yrfi' key must be added alongside, not instead of, 'nrfi'"
        )

    def test_preexisting_native_teamtotals_key_survives_alongside_registry_team_totals(self):
        books = {"kalshi": {"teamTotals": {"away": {"line": 4.5}, "source": "kalshi_native"}}}
        registry = self.make_registry({
            "NYYPHI": {"kalshi_key": "NYYPHI", "markets": {
                "team_total_away": {"team": "NYY", "best_line": {"ticker": "t", "over_n": 4,
                                     "implied_pct": 50.0, "american": -105}, "lines": []},
            }}
        })
        self._write("slate.json", self.make_slate([self.make_game()]))
        self._write("odds.json", self.make_odds([self.make_odds_entry(books=books)]))
        self._write("kalshi_market_registry.json", registry)

        result = self._run()
        assert result.returncode == 0, result.stderr
        kal = self._read_slate()["games"][0]["odds"]["kalshi"]
        assert kal["teamTotals"] == {"away": {"line": 4.5}, "source": "kalshi_native"}
        assert kal["team_totals"]["away"]["line"] == 4

    def test_preexisting_ml_from_native_is_overwritten_by_registry_ml(self):
        """Same key name ('ml') — registry-sourced value must win, matching current behavior."""
        books = {"kalshi": {"ml": {"away": -999, "home": 999, "source": "kalshi_native"}}}
        registry = self.make_registry({
            "NYYPHI": {"kalshi_key": "NYYPHI", "markets": {"moneyline": self.make_ml_market(-115, -105)}}
        })
        self._write("slate.json", self.make_slate([self.make_game()]))
        self._write("odds.json", self.make_odds([self.make_odds_entry(books=books)]))
        self._write("kalshi_market_registry.json", registry)

        result = self._run()
        assert result.returncode == 0, result.stderr
        kal = self._read_slate()["games"][0]["odds"]["kalshi"]
        assert kal["ml"]["away"] == -115 and kal["ml"]["source"] == "kalshi_registry", (
            "registry ML must overwrite native ML when both exist under the same key"
        )


class TestPinVigFreeFieldRemoval(MergeOddsHarness):

    def test_pin_vig_free_is_removed_from_matched_game(self):
        game = self.make_game(extra={"pinVigFree": {"away": 40.0, "home": 60.0}})
        self._write("slate.json", self.make_slate([game]))
        self._write("odds.json", self.make_odds([self.make_odds_entry()]))
        self._write("kalshi_market_registry.json", self.make_registry({}))

        result = self._run()
        assert result.returncode == 0, result.stderr
        g = self._read_slate()["games"][0]
        assert "pinVigFree" not in g


class TestGameStatusInvariance(MergeOddsHarness):
    """merge_odds.py never reads game.status/excludedFromSlate — confirmed by source inspection."""

    @pytest.mark.parametrize("status", ["Postponed", "In Progress", "Final", "Scheduled"])
    def test_status_does_not_affect_odds_merge(self, status):
        game = self.make_game(status=status)
        self._write("slate.json", self.make_slate([game]))
        self._write("odds.json", self.make_odds([self.make_odds_entry(pinnacle_vf={"away": 48.0, "home": 52.0})]))
        self._write("kalshi_market_registry.json", self.make_registry({}))

        result = self._run()
        assert result.returncode == 0, result.stderr
        g = self._read_slate()["games"][0]
        assert g["status"] == status
        assert g["pinnacleVF"] == {"away": 48.0, "home": 52.0}

    def test_excluded_game_still_gets_odds_merged(self):
        game = self.make_game(extra={"excludedFromSlate": True, "exclusionReason": "test"})
        self._write("slate.json", self.make_slate([game]))
        self._write("odds.json", self.make_odds([self.make_odds_entry()]))
        self._write("kalshi_market_registry.json", self.make_registry({}))

        result = self._run()
        assert result.returncode == 0, result.stderr
        g = self._read_slate()["games"][0]
        assert g["excludedFromSlate"] is True
        assert g["kalshiKey"] == "NYYPHI"


class TestMultipleGamesMixedCoverage(MergeOddsHarness):

    def test_ordering_and_independent_coverage_preserved(self):
        g1 = self.make_game("NYY", "PHI", "New York Yankees", "Philadelphia Phillies")
        g2 = self.make_game("BOS", "TB", "Boston Red Sox", "Tampa Bay Rays")
        g3 = self.make_game("LAD", "SD", "Los Angeles Dodgers", "San Diego Padres")

        registry = self.make_registry({
            "NYYPHI": {"kalshi_key": "NYYPHI", "markets": {"moneyline": self.make_ml_market()}},
            # BOSTB deliberately absent from registry.
            "LADSD": {"kalshi_key": "LADSD", "markets": {"moneyline": self.make_ml_market(-200, 170)}},
        })
        self._write("slate.json", self.make_slate([g1, g2, g3]))
        self._write("odds.json", self.make_odds([
            self.make_odds_entry("New York Yankees", "Philadelphia Phillies"),
            self.make_odds_entry("Boston Red Sox", "Tampa Bay Rays"),
            self.make_odds_entry("Los Angeles Dodgers", "San Diego Padres"),
        ]))
        self._write("kalshi_market_registry.json", registry)

        result = self._run()
        assert result.returncode == 0, result.stderr
        games = self._read_slate()["games"]

        matchups = [f"{g['away']['abbr']}@{g['home']['abbr']}" for g in games]
        assert matchups == ["NYY@PHI", "BOS@TB", "LAD@SD"], "game order must be preserved"

        assert games[0]["odds"]["kalshi"]["ml"]["away"] == -110
        assert games[1]["kalshiKey"] == "BOSTB"
        assert games[1]["kalshiVF"] is None  # no registry entry
        assert games[2]["odds"]["kalshi"]["ml"]["away"] == -200


class TestIdempotency(MergeOddsHarness):

    def test_rerun_with_unchanged_inputs_produces_identical_output(self):
        registry = self.make_registry({
            "NYYPHI": {"kalshi_key": "NYYPHI", "markets": {"moneyline": self.make_ml_market()}}
        })
        self._write("slate.json", self.make_slate([self.make_game()]))
        self._write("odds.json", self.make_odds([self.make_odds_entry()]))
        self._write("kalshi_market_registry.json", registry)

        r1 = self._run()
        assert r1.returncode == 0, r1.stderr
        first = self._read_slate()

        r2 = self._run()
        assert r2.returncode == 0, r2.stderr
        second = self._read_slate()

        assert first == second, "rerunning with unchanged inputs must be idempotent"
