# PRODUCTION_INCIDENT_SLATE_FS_IMPORT.md

**Status:** Fixed. Single-incident record for the recurring `Fetch Slate
Data` workflow failure on the "Fetch non-odds data from Vercel" /
`/api/slate` step.

## Summary

`/api/slate` started failing on every invocation while `/api/teamstats`,
`/api/pitchers`, `/api/weather`, and `/api/bullpen` kept succeeding. The
`fetch-slate.yml` step used `curl -sf`, which suppresses the HTTP
status/body on failure, so the workflow log only ever showed `ERROR:
slate fetch failed` — no signal pointing at `api/slate.js` itself.

## Root cause

The Sentinel Single-Source mission (`docs/DUPLICATE_LOGIC_INVENTORY.md`
#2) added, at module scope in `api/slate.js`:

```js
import { readFileSync } from 'fs';
...
const _raw = readFileSync(new URL('../lib/sentinel_constants.json', import.meta.url), 'utf8');
```

wrapped in a `try/catch` intended to fall back safely if the file
couldn't be read. That reasoning was correct for a failure *inside* the
`readFileSync()` call (confirmed locally: even a genuinely-missing target
file falls back cleanly under Node's own ESM loader) — but wrong for a
failure in the top-level `import { readFileSync } from 'fs'` statement
itself. A static ES `import` that a bundler cannot resolve aborts module
evaluation before any function body — including the `try/catch` — ever
runs. `fs` is a Node builtin; `api/slate.js` was, and remains, the only
file anywhere under `api/` that imports any Node builtin module or
touches the filesystem at all (`api/teamstats.js`/`pitchers.js`/
`weather.js`/`bullpen.js` do neither) — this is exactly why those four
endpoints were unaffected while `/api/slate` failed on every request.
The file's own top-of-file comment already stated the intended contract
this violated: "Pure: no I/O, no clock reads, no mutation."

This was never caught before merge because verification at the time used
Node's native ESM loader directly (`node --input-type=module -e
"import ... from './api/slate.js'"`) — a materially different execution
path from Vercel's actual serverless bundling of the deployed function,
and the one environment where `import.meta.url`-relative file access and
Node-builtin-module resolution are least guaranteed to behave the same
as local `node`.

## Fix

Removed the runtime file read entirely. `isSentinelPrice()` now uses only
the hardcoded literal value set (identical to
`lib/sentinel_constants.json`'s content, kept in sync by
`tests/test_sentinel_python_js_parity.py`) — the same values, just
without ever touching the filesystem. `lib/sentinel_validator.py` (the
Python side, a normal server-side script with no bundling concerns) is
unaffected and unchanged — it still loads from the JSON file.

## Regression coverage

`tests/test_slate_no_filesystem_io.py`: a structural guardrail asserting
`api/slate.js` never reintroduces `readFileSync`/`require('fs')`/
`import ... from 'fs'`/`import.meta` (or any other filesystem/Node-
builtin-module access), enforcing the file's own "Pure: no I/O" contract
permanently instead of relying on memory. Also reproduces the exact
missing-target-file scenario (`lib/sentinel_constants.json` absent from
the working tree entirely, simulating a bundler that failed to trace it)
and proves the module still imports and evaluates cleanly.

## Diagnostics fix (independent of the root cause, done first)

`fetch-slate.yml`'s `fetch_endpoint()` helper no longer uses `curl -sf`.
It now captures the HTTP status code and response body separately, and
on failure prints the requested URL, HTTP status (or `TRANSPORT_ERROR` +
curl's own exit code when the status was never received), and a
truncated response body — without ever printing request headers or
secrets. Retry behavior (`--retry 3 --retry-delay 5`) is unchanged; curl
retries transient/5xx statuses independent of the `-f` flag.
