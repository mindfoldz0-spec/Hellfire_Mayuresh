"""Check and optionally tighten the Supabase recordings bucket.

The officer console never needs a publicly accessible bucket: it authenticates
against the gateway and the gateway mints a one-shot signed URL for playback.
Use this script to see whether the bucket is public, and with ``--fix`` to set
it private. It only reads/writes bucket metadata; it never lists or downloads
recording objects.

Usage:
    python scripts/verify_recordings_bucket.py           # report only
    python scripts/verify_recordings_bucket.py --fix     # set bucket private
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Set the bucket to private (requires a service-role key).",
    )
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    url = os.getenv("SUPABASE_URL", "").strip()
    key = (
        os.getenv("SUPABASE_KEY", "").strip()
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    )
    bucket = os.getenv("SUPABASE_BUCKET", "recordings").strip()

    if not url or not key:
        print("SUPABASE_URL and SUPABASE_KEY (or SUPABASE_SERVICE_ROLE_KEY) must be set.")
        return 1

    try:
        from supabase import create_client

        client = create_client(url, key)
        info = client.storage.get_bucket(bucket)
    except Exception as exc:  # network / auth / missing bucket: keep it brief
        print(f"Could not read bucket {bucket!r}: {type(exc).__name__}")
        return 2

    is_public = bool(getattr(info, "public", False))
    print(f"Bucket: {bucket}")
    print(
        "PUBLIC: anyone with an object path can fetch it without signing in."
        if is_public
        else "PRIVATE: objects require a signed URL or service credentials."
    )
    if not is_public:
        return 0
    if not args.fix:
        print("Run again with --fix to set this bucket private.")
        return 3

    try:
        client.storage.update_bucket(bucket, {"public": False})
    except Exception as exc:
        print(f"Could not update bucket {bucket!r}: {type(exc).__name__}")
        return 4

    print("Bucket updated to private. Recordings remain accessible via the gateway.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
