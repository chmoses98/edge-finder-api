# GITHUB_WRITE_GUIDE.md
# Operational guide for Claude write operations to the edge-finder-api repo
# Last updated: June 4, 2026

---

## CRITICAL: SHA Must Be Fetched Fresh Before Every PUT

GitHub's Contents API requires the current file SHA in every PUT request. Using a stale SHA (from earlier in the session or a prior response) causes a 409 Conflict error. This is the single most common write failure.

### Correct Write Pattern

```bash
# Step 1: Fetch the current SHA (always do this immediately before the PUT)
SHA=$(curl -s -H "Authorization: token TOKEN" \
  "https://api.github.com/repos/chmoses98/edge-finder-api/contents/bets.json" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['sha'])")

# Step 2: Prepare the payload
CONTENT=$(base64 -w0 /home/claude/bets_updated.json)

# Step 3: PUT immediately after SHA fetch (do not insert steps between)
curl -s -X PUT \
  -H "Authorization: token TOKEN" \
  -H "Content-Type: application/json" \
  "https://api.github.com/repos/chmoses98/edge-finder-api/contents/bets.json" \
  -d "{\"message\":\"Update bets.json — [date]\",\"content\":\"$CONTENT\",\"sha\":\"$SHA\"}"
```

---

## Large Payload Pattern (bets.json and other large files)

Inline `-d` injection fails at shell argument length limits when bets.json exceeds ~50KB. Use file-based payload instead.

```bash
# Step 1: Fetch SHA
SHA=$(curl -s -H "Authorization: token TOKEN" \
  "https://api.github.com/repos/chmoses98/edge-finder-api/contents/bets.json" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['sha'])")

# Step 2: Build payload as a file (avoids argument length limits)
python3 << PYEOF
import json, base64

with open('/home/claude/bets_updated.json', 'rb') as f:
    content_b64 = base64.b64encode(f.read()).decode()

payload = {
    "message": "Update bets.json — [DATE]",
    "content": content_b64,
    "sha": "$SHA"
}

with open('/home/claude/push_payload.json', 'w') as f:
    json.dump(payload, f)
PYEOF

# Step 3: Send with --data-binary @file (not -d)
curl -s -X PUT \
  -H "Authorization: token TOKEN" \
  -H "Content-Type: application/json" \
  "https://api.github.com/repos/chmoses98/edge-finder-api/contents/bets.json" \
  --data-binary @/home/claude/push_payload.json
```

---

## Pushing Multiple Files in One Session

Each file requires its own SHA fetch and PUT. Do NOT reuse a SHA across files or across multiple pushes of the same file.

Recommended order for a post-game session:
1. Push `bets.json` first (largest, most likely to conflict)
2. Push `BET_LOG.md` second
3. Push model files (RULES.md, MODEL_CORE.md, etc.) if updated

---

## bets.json Structure Note

`bets.json` is a **flat array** of bet objects. Parse it directly:

```python
with open('bets.json') as f:
    bets = json.load(f)           # ← this gives you the list directly

# NOT:
bets = data.get('bets', [])      # ← this is WRONG — there is no 'bets' key
```

When writing back, the root object is the array itself:
```python
with open('bets.json', 'w') as f:
    json.dump(bets_list, f, indent=2)
```

---

## Vercel API — BLOCKED

The Vercel deployment at `https://edge-finder-api.vercel.app` is NOT accessible from Claude's network and returns 403. Do not attempt to call it directly.

All data that the Vercel API would serve is accessed through:
1. The `fetch-slate` GitHub Action (triggers Vercel server-side, then writes results to `data/` in the repo)
2. Reading `data/slate.json`, `data/pitchers.json`, etc. from GitHub after the Action completes

Never call `https://edge-finder-api.vercel.app/*` directly from a Claude session.

---

## GitHub Action Trigger

```bash
# Trigger fetch-slate action
curl -s -X POST \
  -H "Authorization: token TOKEN" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/chmoses98/edge-finder-api/actions/workflows/fetch-slate.yml/dispatches" \
  -d '{"ref":"main","inputs":{"date":"YYYY-MM-DD"}}'

# Wait ~40 seconds, then verify meta.json
curl -s -H "Authorization: token TOKEN" \
  "https://raw.githubusercontent.com/chmoses98/edge-finder-api/main/data/meta.json"
```

---

## Common Errors

| Error | Cause | Fix |
|---|---|---|
| 409 Conflict | Stale SHA | Re-fetch SHA immediately before PUT |
| 422 Unprocessable | Malformed base64 or JSON | Verify payload JSON validity before sending |
| Argument list too long | Large `-d` payload | Use `--data-binary @file` pattern |
| 403 Forbidden | Vercel direct call | Never call Vercel directly — use GitHub Action |
