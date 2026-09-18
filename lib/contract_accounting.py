"""
lib/contract_accounting.py
==========================
ONE definition of "every Kalshi contract attributable to this game is
visible somewhere", shared by the single-game fetch and the real-money
handicapping card.

WHY THIS IS A LIBRARY AND NOT TWO COPIES
----------------------------------------
Both artifacts make the same promise -- "EVERY Kalshi market for this
game is here" -- and both can break it the same way: Kalshi introduces a
family this repository does not recognise, the strict single-game
registry rejects its contracts, and they disappear while the artifact
still *looks* complete because every family we do understand is present.

Two implementations of that promise would drift, and the one that drifts
is the one that silently stops checking. So attribution lives here once.

THE FOUR TERMINAL STATES
------------------------
A raw contract attributable to a game must end in exactly one visible
state. There is no fifth "quietly gone":

  1. NORMALIZED            -- parsed, registry-validated, on the card
  2. REGISTRY_EXCLUDED     -- parsed, rejected by the strict registry,
                              retained with its `exclusionReason`
  3. UNCLASSIFIED          -- the normalizer could not parse it at all,
                              retained with the normalizer's own reason
  4. AMBIGUOUS_ATTRIBUTION -- no event ticker, so it cannot be tied to
                              any game; retained explicitly rather than
                              guessed into or out of one

ATTRIBUTION IS KALSHI'S OWN GROUPING KEY, NOT A GUESS
-----------------------------------------------------
Event tickers are learned from the records that *did* resolve to the
matchup. Every raw contract sharing one of those event tickers is then
required to appear in state 1, 2 or 3. A contract with no event ticker
is state 4 and is never attributed by inference.
"""

ATTRIBUTION_METHOD = "KALSHI_EVENT_TICKER"

#: The invariant a complete artifact must satisfy. Stated as data so the
#: artifact carries the rule it was checked against, not just the verdict.
INVARIANT = "silentRemainderCount == 0"


def raw_ticker(raw):
    """The contract ticker as it appears in a RAW Kalshi payload."""
    return (raw or {}).get("ticker") or (raw or {}).get("market_ticker")


def raw_event_ticker(raw):
    """The event ticker as it appears in a RAW Kalshi payload."""
    return (raw or {}).get("event_ticker") or (raw or {}).get("eventTicker")


def unclassified_raw_contracts(raw_markets, records, normalize_market=None, **normalize_kwargs):
    """
    Pure-ish. The raw contracts that never produced a normalized record.

    `normalize_batch` reports malformed entries as ``(None, reason)`` --
    the ticker is deliberately None, because a record that failed to
    parse has no trustworthy identity. That is correct for the
    normalizer and useless for accounting: a contract with no identity
    cannot be shown to be present.

    So identity is recovered from the RAW payload, which still has it,
    and the reason is recovered by re-normalizing only the handful of
    contracts that failed (normally zero). Passing `normalize_market`
    is optional; without it the reason is reported as
    ``NORMALIZER_REJECTED`` rather than fabricated.

    Returns a list of ``{"ticker", "eventTicker", "reason",
    "state": "UNCLASSIFIED"}``.
    """
    normalized_tickers = {r.get("ticker") for r in records if r.get("ticker")}
    out = []
    for raw in raw_markets:
        ticker = raw_ticker(raw)
        if ticker and ticker in normalized_tickers:
            continue
        reason = "NORMALIZER_REJECTED"
        if normalize_market is not None:
            try:
                _record, _status, why = normalize_market(raw, **normalize_kwargs)
                if why:
                    reason = why
            except Exception as exc:  # noqa: BLE001 - accounting must never crash the build
                reason = f"NORMALIZER_RAISED: {type(exc).__name__}"
        out.append({
            "ticker": ticker,
            "eventTicker": raw_event_ticker(raw),
            "reason": reason,
            "state": "UNCLASSIFIED",
        })
    return out


#: An event key shorter than this is too generic to attribute a contract
#: with. Kalshi's real keys look like ``26SEP171510SDCOL`` (12+ chars).
MIN_EVENT_KEY_LENGTH = 10


def event_key(event_ticker):
    """
    Pure. The GAME part of a Kalshi event ticker, with the family part
    stripped.

    Kalshi names an event ``<SERIES>-<GAMEKEY>``:

        KXMLBGAME-26SEP171510SDCOL       moneyline
        KXMLBF5-26SEP171510SDCOL         first five
        KXMLBTEAMTOTAL-26SEP171510SDCOL  team totals

    The series part changes with the FAMILY; the game key does not. That
    asymmetry is exactly what makes a brand-new family attributable:
    ``KXMLBQUANTUMFLUX-26SEP171510SDCOL`` is a contract on this game even
    though nothing in this repository has ever heard of the series.

    Returns None when there is no usable key, so a malformed ticker can
    never widen a game's attribution.
    """
    if not event_ticker or "-" not in event_ticker:
        return None
    key = event_ticker.split("-", 1)[1].strip().upper()
    return key if len(key) >= MIN_EVENT_KEY_LENGTH else None


def game_event_tickers(kept, excluded=()):
    """
    The event tickers a game is known to own, learned from every
    normalized record that resolved to it -- kept or excluded alike.

    Excluded records count on purpose: if an entire new family is
    rejected by the registry, its event ticker is the only evidence that
    the family belongs to this game at all.
    """
    return {
        r.get("eventTicker")
        for r in list(kept) + list(excluded)
        if r.get("eventTicker")
    }


def game_event_keys(kept, excluded=()):
    """The family-independent game keys behind those event tickers."""
    return {
        key for key in (
            event_key(t) for t in game_event_tickers(kept, excluded)
        ) if key
    }


def attributes_to_game(raw, event_tickers, keys):
    """
    Pure. Does this raw contract belong to the game described by
    `event_tickers` / `keys`?

    TWO routes, and the second is the one that matters:

      1. exact event ticker -- a family already on the card
      2. the family-independent game key -- a family that is NOT, and
         that route is the only reason a newly-introduced Kalshi family
         is *visible* rather than quietly absent

    Without route 2 an unrecognised family is not merely unclassified,
    it is unattributable: it never enters the denominator, so the
    exhaustiveness check passes and the card still claims completeness.
    That is the precise failure this module exists to prevent.
    """
    event_ticker = raw_event_ticker(raw)
    if not event_ticker:
        return False
    if event_ticker in event_tickers:
        return True
    key = event_key(event_ticker)
    return bool(key and key in keys)


def account_for_game_contracts(raw_markets, kept, excluded, all_records, unclassified_raw=()):
    """
    Pure. Prove that EVERY raw Kalshi contract attributable to this game
    is represented somewhere.

    `kept`      -- normalized, registry-validated records on the artifact
    `excluded`  -- normalized records the strict registry rejected, kept
                   with their raw reason
    `all_records` -- every normalized record in the universe (context)
    `unclassified_raw` -- rows from :func:`unclassified_raw_contracts`;
                   contracts the normalizer could not parse, which are
                   still *visible* and therefore still *accounted*

    Returns an accounting dict. ``silentRemainderCount == 0`` is the
    invariant a successful artifact must satisfy; a caller that finds it
    violated must fail loudly rather than write a plausible-looking but
    incomplete artifact.
    """
    kept_tickers = {r.get("ticker") for r in kept if r.get("ticker")}
    excluded_tickers = {r.get("ticker") for r in excluded if r.get("ticker")}
    unclassified_tickers = {
        r.get("ticker") for r in unclassified_raw if r.get("ticker")
    }

    event_tickers = game_event_tickers(kept, excluded)
    game_keys = game_event_keys(kept, excluded)
    # An unparseable or unrecognised contract still carries its event
    # ticker, and that is the only thing tying it to this game. It does
    # NOT get to define a new event ticker or key (that would let a
    # garbage row invent a game), but it IS attributed when it shares a
    # key the parsed records already established.

    attributable, unattributable = [], []
    for raw in raw_markets:
        ticker = raw_ticker(raw)
        if not raw_event_ticker(raw):
            # Only report the ones not already accounted for -- a contract
            # we normalized fine needs no ambiguity note.
            if (ticker and ticker not in kept_tickers
                    and ticker not in excluded_tickers
                    and ticker not in unclassified_tickers):
                unattributable.append(ticker)
            continue
        if attributes_to_game(raw, event_tickers, game_keys):
            attributable.append(ticker)

    attributable_set = {t for t in attributable if t}
    visible = kept_tickers | excluded_tickers | unclassified_tickers
    accounted = attributable_set & visible
    silent_remainder = sorted(attributable_set - accounted)

    return {
        "attributionMethod": ATTRIBUTION_METHOD,
        "rawGameAttributableContracts": len(attributable_set),
        "normalizedMarkets": len(kept_tickers),
        "excludedOrUnresolved": len(excluded_tickers),
        "unclassifiedContracts": len(attributable_set & unclassified_tickers),
        "accountedContracts": len(accounted),
        "silentRemainderCount": len(silent_remainder),
        "silentRemainderTickers": silent_remainder[:50],
        "gameEventTickers": sorted(k for k in event_tickers if k),
        "gameEventKeys": sorted(game_keys),
        # Explicitly retained ambiguity -- never attributed by guessing.
        "unattributableRawContracts": len(unattributable),
        "unattributableSampleTickers": sorted(t for t in unattributable if t)[:20],
        "rawUniverseContracts": len(raw_markets),
        "normalizedUniverseRecords": len(all_records),
        "invariant": INVARIANT,
        "invariantHolds": len(silent_remainder) == 0,
    }


def card_game_accounting(raw_markets, kept, excluded, all_records, unclassified_raw=()):
    """
    The same accounting, under the field names the handicapping card
    publishes. Deliberately a projection of
    :func:`account_for_game_contracts` rather than a second computation,
    so the card and the single-game bundle can never disagree about what
    "accounted for" means.
    """
    detail = account_for_game_contracts(
        raw_markets, kept, excluded, all_records, unclassified_raw=unclassified_raw
    )
    return {
        "attributionMethod": detail["attributionMethod"],
        "rawAttributableCount": detail["rawGameAttributableContracts"],
        "normalizedCount": detail["normalizedMarkets"],
        "excludedOrUnresolvedCount": (
            detail["excludedOrUnresolved"] + detail["unclassifiedContracts"]
        ),
        "registryExcludedCount": detail["excludedOrUnresolved"],
        "unclassifiedCount": detail["unclassifiedContracts"],
        "accountedCount": detail["accountedContracts"],
        "silentRemainderCount": detail["silentRemainderCount"],
        "silentRemainderTickers": detail["silentRemainderTickers"],
        "gameEventTickers": detail["gameEventTickers"],
        "gameEventKeys": detail["gameEventKeys"],
        "invariant": detail["invariant"],
        "invariantHolds": detail["invariantHolds"],
    }
