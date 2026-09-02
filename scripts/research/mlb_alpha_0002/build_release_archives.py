#!/usr/bin/env python3
"""
scripts/research/mlb_alpha_0002/build_release_archives.py
==============================================================
MLB-ALPHA-0002 raw-dataset Release: build BYTE-DETERMINISTIC archives.

WHY THIS IS PYTHON AND NOT `tar`
--------------------------------
The first attempt at this used GNU tar with the usual reproducibility
flags (--sort=name --mtime --owner=0 --group=0 --numeric-owner
--format=gnu, LC_ALL=C). That IS reproducible on ONE machine -- verified
locally byte-identical across two builds after perturbing every source
file's mtime -- but it is NOT reproducible ACROSS machines: the first
real publication run rebuilt the same 58 verified-identical files and
produced different archive hashes than the local build
(candles ff2c26a0... vs 67a0c58b..., trades f0d3cb47... vs ed4fcedf...),
while the single-file gzip asset matched exactly. Per-file SHA256
verification passed on that same run, so the DATA was identical and the
difference was purely tar's own framing (implementation/version-dependent
header details, directory entries, and permission bits inherited from the
checkout).

The fix is to stop delegating archive framing to whatever `tar` happens
to be installed and to write every header field explicitly:

  * members sorted by name (byte-wise, not locale-dependent)
  * mtime = 0 for every member
  * uid = gid = 0, uname = gname = "" (nothing about the builder leaks in)
  * mode forced to 0o644 -- the source files are data, and a checkout's
    permission bits must not change the archive
  * type forced to regular file; NO directory entries are emitted at all
    (they carry mode/mtime of their own and are the least portable part
    of a tar; tarfile.extractall creates parents implicitly, which is
    exactly how hydrate_raw_dataset.py extracts)
  * format = GNU_FORMAT explicitly, so no pax extended headers (which
    would embed atime/ctime) can appear
  * no compression on the .tar itself (the members are already .gz)

gzip metadata for the one gzipped asset is normalized separately by
writing the gzip member with mtime=0 and no embedded filename.

Usage:  build_release_archives.py <history_dir> <out_dir>
Emits:  <out_dir>/mlb-alpha-0002-kalshi-candles-v1.tar
        <out_dir>/mlb-alpha-0002-kalshi-trades-v1.tar
        <out_dir>/recovery_manifest.json.gz
        <out_dir>/SHA256SUMS.txt

RESEARCH ONLY. Reads research artifacts; writes only into <out_dir>.
"""
import gzip
import hashlib
import io
import os
import sys
import tarfile

FIXED_MTIME = 0
FIXED_MODE = 0o644
ARCHIVES = (
    ("candles", "mlb-alpha-0002-kalshi-candles-v1.tar"),
    ("trades", "mlb-alpha-0002-kalshi-trades-v1.tar"),
)


def _members(root, subdir):
    """Every regular file under <root>/<subdir>, as archive-relative paths,
    sorted byte-wise. Sorting on the encoded bytes (not the str) makes the
    order independent of locale and of Python's collation."""
    base = os.path.join(root, subdir)
    found = []
    for dirpath, _dirnames, filenames in os.walk(base):
        for name in filenames:
            full = os.path.join(dirpath, name)
            if not os.path.isfile(full) or os.path.islink(full):
                continue
            rel = os.path.relpath(full, root)
            found.append((rel.encode("utf-8"), rel, full))
    found.sort(key=lambda t: t[0])
    return [(rel, full) for _key, rel, full in found]


def build_tar(root, subdir, out_path):
    members = _members(root, subdir)
    if not members:
        raise SystemExit("::error::no files found under %s/%s" % (root, subdir))
    # format=GNU_FORMAT pinned explicitly; no pax headers, so no atime/ctime.
    with tarfile.open(out_path, "w", format=tarfile.GNU_FORMAT) as tf:
        for rel, full in members:
            info = tarfile.TarInfo(name=rel.replace(os.sep, "/"))
            info.size = os.path.getsize(full)
            info.mtime = FIXED_MTIME
            info.mode = FIXED_MODE
            info.type = tarfile.REGTYPE
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            with open(full, "rb") as fh:
                tf.addfile(info, fh)
    return len(members)


def build_gz(src_path, out_path):
    """gzip with mtime=0 and no embedded filename, so the header carries
    nothing environment-specific."""
    with open(src_path, "rb") as fh:
        payload = fh.read()
    buf = io.BytesIO()
    # filename="" keeps the FNAME field out of the header entirely.
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, compresslevel=9, mtime=0) as gz:
        gz.write(payload)
    with open(out_path, "wb") as out:
        out.write(buf.getvalue())


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: build_release_archives.py <history_dir> <out_dir>")
    root, out_dir = sys.argv[1], sys.argv[2]
    for sub in ("candles", "trades"):
        if not os.path.isdir(os.path.join(root, sub)):
            raise SystemExit("::error::missing %s/%s" % (root, sub))
    manifest_src = os.path.join(root, "recovery_manifest.json")
    if not os.path.isfile(manifest_src):
        raise SystemExit("::error::missing %s" % manifest_src)

    os.makedirs(out_dir, exist_ok=True)
    names = []
    for subdir, asset in ARCHIVES:
        out_path = os.path.join(out_dir, asset)
        n = build_tar(root, subdir, out_path)
        print("%s: %d members" % (asset, n))
        names.append(asset)

    build_gz(manifest_src, os.path.join(out_dir, "recovery_manifest.json.gz"))
    names.append("recovery_manifest.json.gz")

    lines = []
    for asset in names:
        digest = sha256(os.path.join(out_dir, asset))
        lines.append("%s  %s" % (digest, asset))
    with open(os.path.join(out_dir, "SHA256SUMS.txt"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("built deterministic archives in %s:" % out_dir)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
