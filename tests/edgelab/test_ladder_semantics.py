"""Date-aware total-ladder settlement and unit-aware observation quotes (lib/edgelab/research/ladder_semantics.py)."""
import pytest

from lib.edgelab.research import ladder_semantics as LS


def test_rule_boundary_is_exactly_2026_09_01():
    assert LS.ladder_rule_for_game_date("2026-08-31") == "GT"
    assert LS.ladder_rule_for_game_date("2026-09-01") == "GE"
    assert LS.ladder_rule_for_game_date("2026-08-02") == "GT"
    assert LS.ladder_rule_for_game_date("2026-09-21") == "GE"


def test_parse_ladder_ticker():
    assert LS.parse_ladder_ticker("KXMLBTOTAL-26AUG121845CHCWSH-9") == ("KXMLBTOTAL", "26AUG121845CHCWSH", 9, "2026-08-12")
    assert LS.parse_ladder_ticker("KXMLBF5TOTAL-26SEP021335PHIBALG2-5") == ("KXMLBF5TOTAL", "26SEP021335PHIBALG2", 5, "2026-09-02")
    assert LS.parse_ladder_ticker("KXMLBGAME-26AUG121845CHCWSH-CHC") is None
    assert LS.parse_ladder_ticker(None) is None


def test_august_ladder_uses_exact_rung_shift_and_never_guesses():
    arch = {"KXMLBTOTAL-26AUG121845CHCWSH-8": "YES", "KXMLBTOTAL-26AUG121845CHCWSH-9": "NO"}
    # archived(9)=NO under "> 9"; Kalshi pays 9 iff total >= 9, i.e. archived(8)
    assert LS.corrected_ladder_outcome("KXMLBTOTAL-26AUG121845CHCWSH-9", arch) == "YES"
    assert LS.corrected_ladder_outcome("KXMLBTOTAL-26AUG121845CHCWSH-10", arch) == "NO"
    assert LS.corrected_ladder_outcome("KXMLBTOTAL-26AUG121845CHCWSH-12", arch) is None
    assert LS.corrected_ladder_outcome("KXMLBTOTAL-26AUG121845CHCWSH-0", arch) is None


def test_september_ladder_passes_through_unshifted():
    arch = {"KXMLBTOTAL-26SEP121845CHCWSH-8": "YES", "KXMLBTOTAL-26SEP121845CHCWSH-9": "YES",
            "KXMLBTOTAL-26SEP121845CHCWSH-10": "NO"}
    assert LS.corrected_ladder_outcome("KXMLBTOTAL-26SEP121845CHCWSH-9", arch) == "YES"
    assert LS.corrected_ladder_outcome("KXMLBTOTAL-26SEP121845CHCWSH-10", arch) == "NO"


def test_boundary_dates_on_each_side():
    arch = {"KXMLBTOTAL-26AUG311845CHCWSH-7": "YES", "KXMLBTOTAL-26AUG311845CHCWSH-8": "NO",
            "KXMLBTOTAL-26SEP011845CHCWSH-7": "YES", "KXMLBTOTAL-26SEP011845CHCWSH-8": "NO"}
    assert LS.corrected_ladder_outcome("KXMLBTOTAL-26AUG311845CHCWSH-8", arch) == "YES"   # shifted
    assert LS.corrected_ladder_outcome("KXMLBTOTAL-26SEP011845CHCWSH-8", arch) == "NO"    # as archived


def test_non_ladder_and_callable_lookup_pass_through():
    assert LS.corrected_ladder_outcome("KXMLBGAME-26AUG121845CHCWSH-CHC", {"KXMLBGAME-26AUG121845CHCWSH-CHC": "NO"}) == "NO"
    assert LS.corrected_ladder_outcome("KXMLBTOTAL-26AUG121845CHCWSH-9", lambda t: "YES" if t.endswith("-8") else "NO") == "YES"


def test_observation_quote_cents_is_unit_aware():
    assert LS.observation_quote_cents(47.0) == 47
    assert LS.observation_quote_cents(0.47) == 47
    assert LS.observation_quote_cents("0.42") == 42
    assert LS.observation_quote_cents(1.0) is None      # ambiguous
    assert LS.observation_quote_cents(0.0) is None and LS.observation_quote_cents(100) is None
    assert LS.observation_quote_cents(None) is None and LS.observation_quote_cents("x") is None
