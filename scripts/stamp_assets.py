#!/usr/bin/env python
"""Stamp content hashes onto local asset URLs in the HTML pages.

Static assets are served unversioned, so browsers cannot cache them safely and
the HTML previously carried a hand-bumped ``?v=4``. This rewrites every local
``/caller/css/x.css``, ``/officer/js/y.js`` (etc.) reference to include
``?v=<first 10 hex of sha256>``, which lets the static mount serve them as
immutable.

    python scripts/stamp_assets.py          # rewrite in place
    python scripts/stamp_assets.py --check  # exit 1 if anything is stale (CI)
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
FRONTEND = BASE_DIR / "frontend"
PAGES = [FRONTEND / "caller" / "index.html", FRONTEND / "officer" / "index.html"]

# /caller/css/base.css?v=abc123  or  /officer/js/main.js
ASSET_RE = re.compile(
    r'(?P<prefix>(?:href|src)=")(?P<path>/(?:caller|officer)/(?:css|js)/[^"?]+)'
    r'(?:\?v=[A-Za-z0-9]+)?(?P<suffix>")'
)


def asset_hash(url_path: str) -> str:
    file_path = FRONTEND / url_path.lstrip("/")
    if not file_path.is_file():
        return ""
    digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
    return digest[:10]


def stamp(check_only: bool) -> int:
    stale = 0
    for page in PAGES:
        if not page.is_file():
            continue
        original = page.read_text(encoding="utf-8")
        changed = False

        def replace(match: re.Match[str]) -> str:
            nonlocal changed
            url = match.group("path")
            digest = asset_hash(url)
            if not digest:
                return match.group(0)
            candidate = f'{match.group("prefix")}{url}?v={digest}{match.group("suffix")}'
            if candidate != match.group(0):
                changed = True
            return candidate

        updated = ASSET_RE.sub(replace, original)
        if changed:
            stale += 1
            if check_only:
                print(f"STALE {page.relative_to(BASE_DIR)}")
            else:
                page.write_text(updated, encoding="utf-8", newline="\n")
                print(f"stamped {page.relative_to(BASE_DIR)}")

    if check_only:
        if stale:
            print(f"{stale} page(s) have stale asset versions; run scripts/stamp_assets.py")
            return 1
        print("All asset versions are current.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    return stamp(args.check)


if __name__ == "__main__":
    sys.exit(main())
