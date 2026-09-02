#!/usr/bin/env bash
# scripts/research/mlb_alpha_0002/build_release_archives.sh
# ==========================================================
# MLB-ALPHA-0002 raw-dataset Release: build BYTE-DETERMINISTIC archives.
#
# The original publication workflow used a bare `tar -cf`, whose output is
# NOT reproducible: GNU tar records the on-disk readdir order, each
# member's mtime/atime/ctime, and the building user's uid/gid/uname/gname.
# Two runs on two runners therefore produced two different SHA256s for
# byte-identical DATA, which is why the workflow's own hash check had been
# downgraded to "informational". That made the archive hash worthless as
# an integrity control.
#
# This script removes every source of nondeterminism, so the archive hash
# becomes a real, frozen, verifiable identity:
#
#   --sort=name          file ORDER is the sorted path, never readdir order
#   --mtime=UTC 1970-01-01  every member TIMESTAMP is the epoch
#   --owner=0 --group=0  OWNERSHIP is root:root regardless of the builder
#   --numeric-owner      no uname/gname strings leak the building account
#   --format=gnu         one fixed archive FORMAT (no pax extended headers,
#                        which would otherwise embed atime/ctime)
#   LC_ALL=C             byte-wise sort collation, locale-independent
#
# gzip METADATA is normalized separately: `gzip -9 -n` omits the original
# filename and the compression timestamp from the gzip header. (The
# dataset's own *.jsonl.gz payload files are NOT recompressed -- they are
# committed inputs whose bytes are already fixed and per-file hashed; only
# recovery_manifest.json.gz is produced here.)
#
# Usage:  build_release_archives.sh <history_dir> <out_dir>
# Emits:  <out_dir>/mlb-alpha-0002-kalshi-candles-v1.tar
#         <out_dir>/mlb-alpha-0002-kalshi-trades-v1.tar
#         <out_dir>/recovery_manifest.json.gz
#         <out_dir>/SHA256SUMS.txt
#
# RESEARCH ONLY. Reads research artifacts; writes only into <out_dir>.
set -euo pipefail

H="${1:?usage: build_release_archives.sh <history_dir> <out_dir>}"
OUT="${2:?usage: build_release_archives.sh <history_dir> <out_dir>}"

[ -d "$H/candles" ] || { echo "::error::missing $H/candles" >&2; exit 1; }
[ -d "$H/trades" ]  || { echo "::error::missing $H/trades"  >&2; exit 1; }
[ -f "$H/recovery_manifest.json" ] || { echo "::error::missing $H/recovery_manifest.json" >&2; exit 1; }

mkdir -p "$OUT"

# Deterministic tar flags, defined once so the workflow and any local
# reproduction run cannot drift apart.
TAR_DETERMINISTIC=(
  --sort=name
  --mtime=UTC\ 1970-01-01
  --owner=0
  --group=0
  --numeric-owner
  --format=gnu
)

export LC_ALL=C

tar "${TAR_DETERMINISTIC[@]}" -cf "$OUT/mlb-alpha-0002-kalshi-candles-v1.tar" -C "$H" candles
tar "${TAR_DETERMINISTIC[@]}" -cf "$OUT/mlb-alpha-0002-kalshi-trades-v1.tar"  -C "$H" trades

# -n: no original filename, no mtime in the gzip header.
gzip -9 -n -c "$H/recovery_manifest.json" > "$OUT/recovery_manifest.json.gz"

( cd "$OUT" && sha256sum \
    mlb-alpha-0002-kalshi-candles-v1.tar \
    mlb-alpha-0002-kalshi-trades-v1.tar \
    recovery_manifest.json.gz > SHA256SUMS.txt )

echo "built deterministic archives in $OUT:"
cat "$OUT/SHA256SUMS.txt"
