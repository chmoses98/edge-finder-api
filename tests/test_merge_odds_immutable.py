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

import copy
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


class TestF3F7AndPlayerPropsResearchOnly(MergeOddsHarness):
    """
    Market-Universe Parity mission: F3/F7 and the 7 player-prop families
    are RESEARCH-ONLY — build_kalshi_registry.py now archives them
    (Phase 1B fix), and merge_odds.py copies them into slate.json under
    their own keys, clearly separate from the 8 keys
    build_market_ledger.py's REQUIRED_MARKETS/marketLedger actually reads
    (ml/rl/total/team_totals/f5ml/f5_spread/f5_total/nrfi_yrfi). These
    tests prove they reach slate.json, are tagged researchOnly, and never
    displace or get displaced by the core 8.
    """

    def test_f3_f7_and_all_seven_prop_families_reach_slate_tagged_research_only(self):
        registry = self.make_registry({
            "NYYPHI": {
                "kalshi_key": "NYYPHI",
                "markets": {
                    "moneyline": self.make_ml_market(-115, -105),
                    "f3_moneyline": {
                        "prices": {"away": {"american": -130}, "home": {"american": 110},
                                   "tie": {"american": 500}},
                        "away_ticker": "KXMLBF3-TEST-NYY", "home_ticker": "KXMLBF3-TEST-PHI",
                        "tie_ticker": "KXMLBF3-TEST-TIE",
                    },
                    "f7_moneyline": {
                        "prices": {"away": {"american": -140}, "home": {"american": 120}, "tie": None},
                        "away_ticker": "KXMLBF7-TEST-NYY", "home_ticker": "KXMLBF7-TEST-PHI",
                        "tie_ticker": None,
                    },
                    "pitcher_strikeouts": {
                        "family": "pitcher_strikeouts", "unparseableCount": 0,
                        "players": {"NYY:Gerrit Cole": {"displayName": "Gerrit Cole", "team": "NYY",
                                                          "thresholds": [{"threshold": 6, "american": -110}]}},
                    },
                    "pitcher_outs": {"family": "pitcher_outs", "unparseableCount": 0, "players": {
                        "NYY:Gerrit Cole": {"displayName": "Gerrit Cole", "team": "NYY", "thresholds": []}}},
                    "hitter_hits": {"family": "hitter_hits", "unparseableCount": 0, "players": {
                        "PHI:Bryce Harper": {"displayName": "Bryce Harper", "team": "PHI", "thresholds": []}}},
                    "hitter_total_bases": {"family": "hitter_total_bases", "unparseableCount": 0, "players": {
                        "PHI:Bryce Harper": {"displayName": "Bryce Harper", "team": "PHI", "thresholds": []}}},
                    "hitter_hits_runs_rbis": {"family": "hitter_hits_runs_rbis", "unparseableCount": 0, "players": {
                        "PHI:Bryce Harper": {"displayName": "Bryce Harper", "team": "PHI", "thresholds": []}}},
                    "hitter_rbis": {"family": "hitter_rbis", "unparseableCount": 0, "players": {
                        "PHI:Bryce Harper": {"displayName": "Bryce Harper", "team": "PHI", "thresholds": []}}},
                    "hitter_stolen_bases": {"family": "hitter_stolen_bases", "unparseableCount": 0, "players": {
                        "PHI:Bryce Harper": {"displayName": "Bryce Harper", "team": "PHI", "thresholds": []}}},
                },
            }
        })
        self._write("slate.json", self.make_slate([self.make_game()]))
        self._write("odds.json", self.make_odds([self.make_odds_entry()]))
        self._write("kalshi_market_registry.json", registry)

        result = self._run()
        assert result.returncode == 0, result.stderr
        kal = self._read_slate()["games"][0]["odds"]["kalshi"]

        assert kal["f3ml"]["away"] == -130 and kal["f3ml"]["tie"] == 500
        assert kal["f3ml"]["researchOnly"] is True
        assert kal["f7ml"]["away"] == -140 and kal["f7ml"]["tie"] is None
        assert kal["f7ml"]["researchOnly"] is True

        for mkt_key in ("pitcher_strikeouts", "pitcher_outs", "hitter_hits", "hitter_total_bases",
                         "hitter_hits_runs_rbis", "hitter_rbis", "hitter_stolen_bases"):
            assert mkt_key in kal, f"{mkt_key} missing from slate.json"
            assert kal[mkt_key]["researchOnly"] is True
            assert kal[mkt_key]["players"], f"{mkt_key} lost its player ladder"

        # The core 8 real-money-eligible keys are completely unaffected.
        assert kal["ml"]["away"] == -115 and kal["ml"]["home"] == -105

    def test_empty_player_prop_ladder_is_not_written_at_all(self):
        """A prop family present in the registry with zero parsed players
        (e.g. every ticker was unparseable) must not create a fake, empty
        entry in slate.json -- absence is the honest signal, not a
        populated-but-empty block that looks like real research data."""
        registry = self.make_registry({
            "NYYPHI": {"kalshi_key": "NYYPHI", "markets": {
                "moneyline": self.make_ml_market(),
                "pitcher_strikeouts": {"family": "pitcher_strikeouts", "unparseableCount": 3, "players": {}},
            }}
        })
        self._write("slate.json", self.make_slate([self.make_game()]))
        self._write("odds.json", self.make_odds([self.make_odds_entry()]))
        self._write("kalshi_market_registry.json", registry)

        result = self._run()
        assert result.returncode == 0, result.stderr
        kal = self._read_slate()["games"][0]["odds"]["kalshi"]
        assert "pitcher_strikeouts" not in kal

    def test_f3_f7_absent_from_registry_leaves_slate_keys_absent(self):
        registry = self.make_registry({
            "NYYPHI": {"kalshi_key": "NYYPHI", "markets": {"moneyline": self.make_ml_market()}}
        })
        self._write("slate.json", self.make_slate([self.make_game()]))
        self._write("odds.json", self.make_odds([self.make_odds_entry()]))
        self._write("kalshi_market_registry.json", registry)

        result = self._run()
        assert result.returncode == 0, result.stderr
        kal = self._read_slate()["games"][0]["odds"]["kalshi"]
        assert "f3ml" not in kal
        assert "f7ml" not in kal


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


class TestRFIFallbackStdoutOrderPreserved(MergeOddsHarness):
    """
    Pre-merge hardening addition. The refactor moved RFI-fallback
    diagnostic prints from inline-during-the-loop to
    "collect into log_lines, print all after merge_odds_immutable()
    returns" (see compute_game_odds_fields()/merge_odds_immutable()).
    Since no other print statement fires between two games' RFI messages
    in either version, this reordering is invisible on stdout — this
    test proves that explicitly across multiple games rather than
    leaving it as an unverified claim.
    """

    def test_multi_game_rfi_fallback_messages_appear_in_game_order(self):
        g1 = self.make_game("NYY", "PHI", "New York Yankees", "Philadelphia Phillies")
        g2 = self.make_game("BOS", "TB", "Boston Red Sox", "Tampa Bay Rays")

        # Neither game's registry entry has an 'rfi' block -> both fall
        # back to the kalshi_search.json index, each producing one
        # distinguishable log line ("OK" for g1's key, "MISSING PRICES"
        # for g2's key, since g2's market has no usable price fields).
        registry = self.make_registry({
            "NYYPHI": {"kalshi_key": "NYYPHI", "markets": {"moneyline": self.make_ml_market()}},
            "BOSTB": {"kalshi_key": "BOSTB", "markets": {"moneyline": self.make_ml_market()}},
        })
        self._write("slate.json", self.make_slate([g1, g2]))
        self._write("odds.json", self.make_odds([
            self.make_odds_entry("New York Yankees", "Philadelphia Phillies"),
            self.make_odds_entry("Boston Red Sox", "Tampa Bay Rays"),
        ]))
        self._write("kalshi_market_registry.json", registry)
        self._write("kalshi_search.json", {
            "date": "2026-07-27",
            "markets": [
                {
                    "event_ticker": "KXMLBRFI-26JUN121840NYYPHI",
                    "market_ticker": "KXMLBRFI-26JUN121840NYYPHI",
                    "market_type": "nrfi_yrfi", "status": "active",
                    "yes_bid": 0.47, "yes_ask": 0.49, "mid": 0.48,
                    "implied_pct": 48.0, "american_odds": 108,
                },
                {
                    # No 'mid' field -> _build_rfi_from_ks_market returns
                    # None -> "MISSING PRICES" log line for BOSTB.
                    "event_ticker": "KXMLBRFI-26JUN121840BOSTB",
                    "market_ticker": "KXMLBRFI-26JUN121840BOSTB",
                    "market_type": "nrfi_yrfi", "status": "active",
                },
            ],
            "results": [],
        })

        result = self._run()
        assert result.returncode == 0, result.stderr

        ok_pos = result.stdout.find("RFI fallback OK: NYYPHI")
        missing_pos = result.stdout.find("RFI fallback MISSING PRICES for BOSTB")
        assert ok_pos != -1 and missing_pos != -1, (
            f"expected both RFI fallback lines in stdout:\n{result.stdout}"
        )
        assert ok_pos < missing_pos, (
            "RFI fallback log lines must appear in the same order as the "
            "games list (NYY@PHI before BOS@TB), matching the original "
            "implementation's inline-per-game print order"
        )


class TestAliasingAndIdentity:
    """
    Pre-merge hardening addition (PR #5 review, Section C). Uses
    object-identity checks (`is`/mutation-after-return), not just value
    equality, to prove the pure-transform functions never share mutable
    state with their inputs. Imports scripts/merge_odds.py's module
    namespace directly (via importlib, with cwd redirected to an
    isolated tmp dir containing minimal valid fixtures so the script's
    top-level load/write code executes harmlessly) so
    compute_game_odds_fields()/merge_odds_immutable() can be called
    in-process with hand-built fixtures — a subprocess+JSON-round-trip
    test (the pattern used elsewhere in this file) cannot observe object
    identity, since JSON serialization always produces new objects.
    """

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.data_dir = os.path.join(self.tmp, "data")
        os.makedirs(self.data_dir)
        with open(os.path.join(self.data_dir, "odds.json"), "w") as f:
            json.dump({"games": []}, f)
        with open(os.path.join(self.data_dir, "slate.json"), "w") as f:
            json.dump({"date": "2026-07-27", "games": []}, f)
        with open(os.path.join(self.data_dir, "kalshi_market_registry.json"), "w") as f:
            json.dump({"registry": {}}, f)
        self._orig_cwd = os.getcwd()
        os.chdir(self.tmp)

        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "merge_odds_identity_test", os.path.join(SCRIPTS_DIR, "merge_odds.py")
        )
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)  # runs the script once against the empty fixtures above

    def teardown_method(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _game(self, away_abbr="NYY", home_abbr="PHI", away_team="New York Yankees", home_team="Philadelphia Phillies"):
        return {
            "away": {"abbr": away_abbr, "team": away_team},
            "home": {"abbr": home_abbr, "team": home_team},
        }

    def _odds_entry(self, away_team="New York Yankees", home_team="Philadelphia Phillies", books=None):
        return {
            "awayTeam": away_team, "homeTeam": home_team,
            "books": books if books is not None else {},
            "pinnacleVF": None, "pinnacleF5VF": None,
            "eventId": "e1", "commenceTime": "2026-07-27T23:05:00Z",
        }

    def _registry(self, key="NYYPHI"):
        return {
            key: {
                "kalshi_key": key,
                "markets": {"moneyline": {
                    "prices": {"away": {"american": -110}, "home": {"american": -110}},
                    "away_ticker": "AWY", "home_ticker": "HOM",
                }},
            }
        }

    def test_input_game_dict_is_never_mutated(self):
        game = self._game()
        game["pinVigFree"] = {"stale": True}
        snapshot = copy.deepcopy(game)
        odds_games = [self._odds_entry()]
        new_game, matched, _, _ = self.mod.compute_game_odds_fields(game, odds_games, self._registry(), {})
        assert matched
        assert game == snapshot, "compute_game_odds_fields must never mutate its `game` argument"
        assert "pinVigFree" not in new_game, "pinVigFree must be removed from the OUTPUT"
        assert "pinVigFree" in game, "...without touching the caller's original dict"

    def test_input_odds_games_list_and_entries_never_mutated(self):
        game = self._game()
        odds_games = [self._odds_entry(books={"pinnacle": {"ml": {"away": -110}}})]
        snapshot = copy.deepcopy(odds_games)
        self.mod.compute_game_odds_fields(game, odds_games, self._registry(), {})
        assert odds_games == snapshot, "odds_games input must never be mutated"

    def test_returned_odds_dict_is_not_the_same_object_as_input_books(self):
        game = self._game()
        entry = self._odds_entry(books={"pinnacle": {"ml": {"away": -110}}})
        odds_games = [entry]
        new_game, matched, _, _ = self.mod.compute_game_odds_fields(game, odds_games, self._registry(), {})
        assert matched
        assert new_game["odds"] is not entry["books"], (
            "the output's 'odds' dict must be a copy, never the same object as "
            "the matched odds.json entry's 'books' dict (the original aliasing bug)"
        )
        assert new_game["odds"]["kalshi"] is not entry["books"].get("kalshi"), (
            "the output's 'odds.kalshi' dict must be independently owned"
        )

    def test_mutating_returned_game_kalshi_block_does_not_mutate_input_odds_entry(self):
        game = self._game()
        entry = self._odds_entry()
        odds_games = [entry]
        new_game, matched, _, _ = self.mod.compute_game_odds_fields(game, odds_games, self._registry(), {})
        assert matched
        new_game["odds"]["kalshi"]["ml"]["away"] = -999999
        assert entry["books"].get("kalshi", {}).get("ml", {}).get("away") != -999999, (
            "mutating the returned game's odds.kalshi block must never leak back "
            "into the input odds.json entry"
        )

    def test_mutating_input_odds_entry_after_transform_does_not_affect_returned_game(self):
        game = self._game()
        entry = self._odds_entry()
        odds_games = [entry]
        new_game, matched, _, _ = self.mod.compute_game_odds_fields(game, odds_games, self._registry(), {})
        assert matched
        before = json.loads(json.dumps(new_game["odds"]["kalshi"]))
        entry["books"]["kalshi"] = {"tampered": True}
        after = new_game["odds"]["kalshi"]
        assert after == before, (
            "mutating the input odds entry AFTER the transform has already run "
            "must never retroactively change the already-returned game's data"
        )

    def test_unrelated_books_are_preserved_alongside_independent_kalshi_copy(self):
        game = self._game()
        entry = self._odds_entry(books={
            "pinnacle": {"ml": {"away": -115, "home": -105}},
            "fanduel": {"ml": {"away": -120, "home": -100}},
        })
        odds_games = [entry]
        new_game, matched, _, _ = self.mod.compute_game_odds_fields(game, odds_games, self._registry(), {})
        assert matched
        assert new_game["odds"]["pinnacle"] == {"ml": {"away": -115, "home": -105}}
        assert new_game["odds"]["fanduel"] == {"ml": {"away": -120, "home": -100}}
        assert new_game["odds"]["kalshi"]["ml"]["away"] == -110  # registry-sourced, present

    def test_preexisting_native_kalshi_block_is_copied_not_aliased(self):
        """
        The TOP-LEVEL 'kalshi' dict itself is always independently owned
        (copied) — proven by identity below. But per the shallow-copy
        boundary contract (docs/IMMUTABLE_PIPELINE.md §10), a NESTED key
        merge_odds.py never itself writes into remains shared by
        reference with the original input. The native 'nrfi' key (set
        only by api/odds.js, NEVER written by merge_odds.py — the
        registry path writes the differently-named 'nrfi_yrfi' key
        instead) is exactly such a key: this test confirms it is
        preserved by value, but is legitimately still the SAME nested
        object as the input's, not a bug. This is safe in production
        because no downstream script (verified via grep across scripts/
        and lib/) ever mutates games[].odds.kalshi.nrfi or .teamTotals in
        place — scripts/validate_odds.py only reads them, and
        capture_closing_lines.py's own 'nrfi' key lives on an unrelated
        local snapshot dict, not this one.
        """
        game = self._game()
        native_kalshi = {"nrfi": {"nrfi": -150, "source": "kalshi_native"}}
        entry = self._odds_entry(books={"kalshi": native_kalshi})
        odds_games = [entry]
        new_game, matched, _, _ = self.mod.compute_game_odds_fields(game, odds_games, self._registry(), {})
        assert matched
        assert new_game["odds"]["kalshi"] is not native_kalshi, (
            "the top-level 'kalshi' dict must always be independently owned"
        )
        assert new_game["odds"]["kalshi"]["nrfi"] == {"nrfi": -150, "source": "kalshi_native"}, (
            "pre-existing native content must be preserved by value"
        )
        assert new_game["odds"]["kalshi"]["nrfi"] is native_kalshi["nrfi"], (
            "a nested key merge_odds.py never writes into (native 'nrfi') is "
            "expected to remain a shared reference per the shallow-copy "
            "boundary contract -- this is documented and safe, not a bug"
        )

    def test_registry_written_ml_key_replaces_preexisting_native_ml_independently(self):
        """
        Unlike 'nrfi' above, the 'ml' key IS written by the registry path
        (kalshi_books['ml'] = {...}, a fresh dict literal) even when
        api/odds.js already populated a native 'ml' block first — so
        'ml' must end up fully independent of whatever was there before,
        with no shared reference to the pre-existing native content.
        """
        game = self._game()
        native_ml = {"away": -105, "home": -115, "source": "kalshi_native"}
        entry = self._odds_entry(books={"kalshi": {"ml": native_ml}})
        odds_games = [entry]
        new_game, matched, _, _ = self.mod.compute_game_odds_fields(game, odds_games, self._registry(), {})
        assert matched
        assert new_game["odds"]["kalshi"]["ml"] is not native_ml
        assert new_game["odds"]["kalshi"]["ml"]["away"] == -110, "registry-sourced value must win"
        assert new_game["odds"]["kalshi"]["ml"].get("source") == "kalshi_registry"

    def test_shared_odds_entry_across_two_games_does_not_cross_contaminate(self):
        """
        Two different slate games that both match the SAME odds.json
        entry (e.g. a fuzzy-matching edge case) must get fully
        independent output odds/kalshi blocks — mutating one must never
        affect the other, and neither call may mutate the shared input.
        """
        shared_entry = self._odds_entry(books={"kalshi": {"native": {"x": 1}}})
        odds_games = [shared_entry]
        registry = self._registry()

        game_a = self._game("NYY", "PHI", "New York Yankees", "Philadelphia Phillies")
        game_b = self._game("NYY", "PHI", "New York Yankees", "Philadelphia Phillies")

        new_a, matched_a, _, _ = self.mod.compute_game_odds_fields(game_a, odds_games, registry, {})
        new_b, matched_b, _, _ = self.mod.compute_game_odds_fields(game_b, odds_games, registry, {})
        assert matched_a and matched_b

        assert new_a["odds"] is not new_b["odds"]
        assert new_a["odds"]["kalshi"] is not new_b["odds"]["kalshi"]

        new_a["odds"]["kalshi"]["ml"]["away"] = -424242
        assert new_b["odds"]["kalshi"]["ml"]["away"] != -424242, (
            "mutating game A's output must not leak into game B's independently "
            "computed output, even though both matched the identical shared "
            "odds.json entry object"
        )
        assert shared_entry["books"]["kalshi"] == {"native": {"x": 1}}, (
            "neither call may have mutated the shared input entry itself"
        )

    def test_merge_odds_immutable_returned_slate_odds_independent_of_input(self):
        slate = {"date": "2026-07-27", "games": [self._game()]}
        entry = self._odds_entry()
        odds_games = [entry]
        new_slate, matched, unmatched, _ = self.mod.merge_odds_immutable(slate, odds_games, self._registry(), {})
        assert matched == 1 and unmatched == []

        new_slate["games"][0]["odds"]["kalshi"]["ml"]["away"] = -55555
        assert entry["books"].get("kalshi", {}).get("ml", {}).get("away") != -55555, (
            "mutating the returned slate must not mutate the supplied odds input"
        )

        entry["books"]["kalshi"] = {"ml": {"away": -66666}}
        assert new_slate["games"][0]["odds"]["kalshi"]["ml"]["away"] == -55555, (
            "mutating the supplied odds input after transformation must not "
            "retroactively mutate the already-returned slate"
        )

        assert slate["games"][0] is not new_slate["games"][0], (
            "merge_odds_immutable must return a new list of new game dicts, "
            "never the same game objects from the input slate"
        )
