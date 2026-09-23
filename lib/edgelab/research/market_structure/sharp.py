"""
Sportsbook (The Odds API decimal odds) -> no-vig probabilities and join to
Kalshi moneyline / total tickers.  Pure functions; as-of time is the
capture time of the run that fetched both legs.
"""
from datetime import datetime, timezone

# Odds API full names -> Kalshi abbreviations (lib.edgelab.mlb_alpha_identity.TEAM_ID_TO_ABBR vocabulary)
TEAM_NAME_TO_ABBR = {
    "Arizona Diamondbacks": "AZ", "Atlanta Braves": "ATL", "Baltimore Orioles": "BAL", "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC", "Chicago White Sox": "CWS", "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL", "Detroit Tigers": "DET", "Houston Astros": "HOU", "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD", "Miami Marlins": "MIA", "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN", "New York Mets": "NYM", "New York Yankees": "NYY", "Oakland Athletics": "ATH",
    "Athletics": "ATH", "Philadelphia Phillies": "PHI", "Pittsburgh Pirates": "PIT", "San Diego Padres": "SD",
    "San Francisco Giants": "SF", "Seattle Mariners": "SEA", "St. Louis Cardinals": "STL", "Tampa Bay Rays": "TB",
    "Texas Rangers": "TEX", "Toronto Blue Jays": "TOR", "Washington Nationals": "WSH",
}


def novig_two_way(dec_a, dec_b):
    """Multiplicative de-vig of two decimal prices -> (p_a, p_b) or None."""
    try:
        ia, ib = 1.0 / float(dec_a), 1.0 / float(dec_b)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    s = ia + ib
    if s <= 0:
        return None
    return ia / s, ib / s


def book_h2h(bookmaker, away_name, home_name):
    """-> (p_away, p_home, last_update) for one bookmaker dict from the Odds API row, or None."""
    for m in bookmaker.get("markets") or []:
        if m.get("key") != "h2h":
            continue
        prices = {o.get("name"): o.get("price") for o in m.get("outcomes") or []}
        if away_name in prices and home_name in prices:
            nv = novig_two_way(prices[away_name], prices[home_name])
            if nv:
                return nv[0], nv[1], m.get("last_update") or bookmaker.get("last_update")
    return None


def book_total(bookmaker):
    """-> (point, p_over_novig, last_update) or None."""
    for m in bookmaker.get("markets") or []:
        if m.get("key") != "totals":
            continue
        over = under = None
        pt = None
        for o in m.get("outcomes") or []:
            if o.get("name") == "Over":
                over, pt = o.get("price"), o.get("point")
            elif o.get("name") == "Under":
                under = o.get("price")
        if over and under and pt is not None:
            nv = novig_two_way(over, under)
            if nv:
                return float(pt), nv[0], m.get("last_update") or bookmaker.get("last_update")
    return None


def odds_row_probabilities(row):
    """
    Odds API event row -> {book_key: {"pAway","pHome","lastUpdate","totalPoint","pOver"}} plus
    awayAbbr/homeAbbr/commenceTs.  Books missing h2h are omitted.
    """
    away, home = row.get("away"), row.get("home")
    out = {"awayAbbr": TEAM_NAME_TO_ABBR.get(away), "homeAbbr": TEAM_NAME_TO_ABBR.get(home),
           "commenceTs": None, "books": {}}
    ct = row.get("commenceTime")
    if ct:
        try:
            out["commenceTs"] = int(datetime.strptime(ct[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            pass
    for b in row.get("bookmakers") or []:
        h = book_h2h(b, away, home)
        t = book_total(b)
        if h is None and t is None:
            continue
        entry = {}
        if h:
            entry.update(pAway=h[0], pHome=h[1], lastUpdate=h[2])
        if t:
            entry.update(totalPoint=t[0], pOver=t[1], totalLastUpdate=t[2])
        out["books"][b.get("key")] = entry
    return out


def consensus(books, keys=("pinnacle", "draftkings", "fanduel", "betmgm")):
    """Mean no-vig home probability over the books present; returns (p_home, n_books)."""
    vals = [books[k]["pHome"] for k in keys if k in books and books[k].get("pHome") is not None]
    if not vals:
        return None, 0
    return sum(vals) / len(vals), len(vals)


def kalshi_fair_mid(yes_bid, yes_ask):
    if yes_bid is None or yes_ask is None:
        return None
    return (float(yes_bid) + float(yes_ask)) / 2.0
