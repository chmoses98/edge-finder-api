import json, sys

try:
    with open('data/slate.json') as f:
        d = json.load(f)
except json.JSONDecodeError as e:
    print(f'INVALID JSON: {e}')
    print('Slate data is malformed — aborting pipeline')
    sys.exit(1)
except Exception as e:
    print(f'ERROR reading slate.json: {e}')
    sys.exit(1)

if d.get('error'):
    print(f'Slate endpoint returned error: {d["error"]}')
    sys.exit(1)

games = d.get('games', [])
print(f'slate.json OK: {len(games)} games')
if len(games) == 0:
    print('WARNING: No games in slate — check date or Vercel endpoint')
