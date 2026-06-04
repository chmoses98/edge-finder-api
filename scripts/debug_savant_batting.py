"""Debug script: test savant_batting endpoint and print response details."""
import urllib.request, json, sys

url = 'https://edge-finder-api.vercel.app/api/savant_batting?year=2026'
try:
    req = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read())
    print('ok:', d.get('ok'))
    print('teamCount:', d.get('teamCount'))
    print('batterCount:', d.get('batterCount'))
    print('xstatsRows:', d.get('xstatsRows'))
    print('fbRows:', d.get('fbRows'))
    print('xstatsHeaders:', d.get('xstatsHeaders'))
    print('fbHeaders:', d.get('fbHeaders'))
    print('error:', d.get('error'))
    teams = d.get('teams', {})
    if teams:
        k = list(teams.keys())[0]
        print(f'Sample team ({k}): xwoba={teams[k].get("xwoba")} fbPct={teams[k].get("fbPct")}')
    else:
        print('NO TEAMS DATA')
except Exception as e:
    print(f'Request failed: {e}')
    sys.exit(1)
