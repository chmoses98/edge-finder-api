#!/usr/bin/env bash
# MRV prospective collector v1 -- ALWAYS-ON external runner (primary cadence path).
# RESEARCH ONLY. READ-ONLY public endpoints. Commits ONLY to research/mrv-prospective-v1.
#
# Owner deployment (one-time), on any Linux host with python3 >= 3.11 and git:
#   export ODDS_API_KEY=...                       # same key the repo's Actions use
#   export MRV_GIT_TOKEN=<fine-grained PAT with contents:write on this repo>
#   git clone https://github.com/chmoses98/edge-finder-api.git && cd edge-finder-api
#   nohup scripts/research/mrv_collector/external_runner.sh > mrv_runner.log 2>&1 &
# Or as a systemd service / container running the same command. The runner
# never touches main: it checks out the research branch for the data path and
# always takes collector CODE from origin/main.
set -euo pipefail
cd "$(dirname "$0")/../../.."
BRANCH="${MRV_BRANCH:-research/mrv-prospective-v1}"
CADENCE="${MRV_CADENCE_MINUTES:-10}"
if [ -n "${MRV_GIT_TOKEN:-}" ]; then
  git remote set-url origin "https://x-access-token:${MRV_GIT_TOKEN}@github.com/chmoses98/edge-finder-api.git"
fi
git config user.name  "mrv-external-runner"
git config user.email "mrv-external-runner@users.noreply.github.com"
git fetch origin main
git fetch origin "$BRANCH" && git checkout -q "$BRANCH" || git checkout -q -b "$BRANCH"
git checkout origin/main -- lib scripts data/edgelab/research_artifacts/mrv_prospective/mrv_series_policy.json
git reset -q lib scripts data/edgelab/research_artifacts/mrv_prospective/mrv_series_policy.json
export GITHUB_EVENT_NAME="external_runner"
while true; do
  # 340-minute budget per loop, then refresh code from main and continue; the loop persists after every cycle.
  python3 scripts/research/mrv_collector/run_loop.py --budget-minutes 340 --cadence-minutes "$CADENCE" --branch "$BRANCH" || true
  python3 scripts/research/mrv_collector/build_health.py --write >/dev/null || true
  python3 scripts/ci/git_data_commit.py --message "MRV prospective health $(date -u +'%Y-%m-%dT%H:%M:%SZ')" --branch "$BRANCH" data/edgelab/research_artifacts/mrv_prospective/v1/health || true
  git fetch origin main && git checkout origin/main -- lib scripts data/edgelab/research_artifacts/mrv_prospective/mrv_series_policy.json && git reset -q lib scripts data/edgelab/research_artifacts/mrv_prospective/mrv_series_policy.json || true
done
