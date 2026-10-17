#!/usr/bin/env bash
# Checks out the `projection-data` branch (the immutable archive) into $1.
#   setup_archive.sh <dir> <create_if_missing: true|false>
# Existing branch -> shallow clone. Missing branch -> an orphan repo with one empty commit; when create_if_missing=true the
# branch is also pushed (first live run only; pull_request dry-runs never push and need no write permission).
set -euo pipefail
DIR="$1"
CREATE="${2:-false}"
REPO_URL="https://github.com/${GITHUB_REPOSITORY}.git"
AUTH="AUTHORIZATION: basic $(printf 'x-access-token:%s' "${GITHUB_TOKEN}" | base64 -w0)"
rm -rf "$DIR"
if git -c "http.extraheader=${AUTH}" ls-remote --exit-code --heads "$REPO_URL" projection-data >/dev/null 2>&1; then
  git -c "http.extraheader=${AUTH}" clone --quiet --depth 1 --branch projection-data "$REPO_URL" "$DIR"
  echo "projection-data: cloned existing branch"
else
  echo "projection-data: branch missing; creating an orphan"
  git init -q -b projection-data "$DIR"
  git -C "$DIR" remote add origin "$REPO_URL"
  git -C "$DIR" -c user.name="github-actions[bot]" -c user.email="github-actions[bot]@users.noreply.github.com" \
    commit -q --allow-empty -m "projection-data: initial empty commit (immutable archive; written only by projection-foundation workflows)"
  if [ "$CREATE" = "true" ]; then
    git -C "$DIR" -c "http.extraheader=${AUTH}" push origin HEAD:projection-data
    git -C "$DIR" -c "http.extraheader=${AUTH}" fetch --quiet origin projection-data
    git -C "$DIR" branch --set-upstream-to=origin/projection-data projection-data >/dev/null
  fi
fi
# later pushes (scripts/ci/git_data_commit.py) authenticate the same way actions/checkout does
git -C "$DIR" config --local "http.https://github.com/.extraheader" "${AUTH}"
git -C "$DIR" config --local user.name "github-actions[bot]"
git -C "$DIR" config --local user.email "github-actions[bot]@users.noreply.github.com"
