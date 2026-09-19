#!/usr/bin/env python3
"""Copy the newest verified backup to S3, and prove the copy is whole.

Usage:
    python scripts/offsite_backup.py              # the newest dump in backups/
    python scripts/offsite_backup.py --file backups/fcp-20260919T100026Z.dump
    python scripts/offsite_backup.py --check      # list what is in the bucket, upload nothing

The nightly dump (`scripts/backup_db.sh`) is verified and kept for fourteen
days, but on the same disk as the database it backs up: a lost VPS loses both.
This puts each night's dump in an S3 bucket as well, under
`<prefix>YYYY/MM/<file>`, encrypted at rest by S3, and then reads the object's
size back and refuses to call it done unless it matches. Retention is the
bucket's lifecycle rule, not this script (docs/offsite_backups.md).

A successful copy writes `logs/offsite-last-ok` (the file name and the time),
which the watchdog reads. Unconfigured (no bucket or keys) prints one line
and exits 0, so the nightly backup runs exactly as before until the four
settings are in `.env`.

Exit codes: 0 copied (or not configured), 1 the copy failed or did not verify.
Never prints a key; an error names its class and S3's error code only.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings, get_settings

REPO = Path(__file__).resolve().parents[1]
BACKUPS = REPO / "backups"
MARKER = REPO / "logs" / "offsite-last-ok"


def configured(settings: Settings) -> bool:
    return bool(
        settings.fcp_s3_bucket and settings.aws_access_key_id and settings.aws_secret_access_key
    )


def client(settings: Settings) -> Any:
    import boto3  # type: ignore[import-untyped]

    return boto3.client(
        "s3",
        region_name=settings.fcp_s3_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
    )


def key_for(prefix: str, dump: Path) -> str:
    """`<prefix>YYYY/MM/<file>`, dated from the dump's own UTC stamp."""
    stamp = dump.name.removeprefix("fcp-")[:8]
    year, month = (stamp[:4], stamp[4:6]) if stamp.isdigit() else ("undated", "00")
    return f"{prefix.rstrip('/')}/{year}/{month}/{dump.name}"


def newest_dump(folder: Path) -> Path | None:
    return max(folder.glob("fcp-*.dump"), key=lambda p: p.stat().st_mtime, default=None)


def describe(exc: BaseException) -> str:
    """An error as its class and, for S3, its error code: never its text."""
    code = (
        getattr(exc, "response", {}).get("Error", {}).get("Code")
        if hasattr(exc, "response")
        else None
    )
    return f"{type(exc).__name__}{f' ({code})' if code else ''}"


def copy(s3: Any, bucket: str, key: str, dump: Path) -> None:
    """Upload, then read the size back; raise unless it matches."""
    s3.upload_file(str(dump), bucket, key, ExtraArgs={"ServerSideEncryption": "AES256"})
    head = s3.head_object(Bucket=bucket, Key=key)
    if int(head["ContentLength"]) != dump.stat().st_size:
        raise RuntimeError("size mismatch after upload")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", type=Path, help="the dump to copy (default: the newest)")
    ap.add_argument("--check", action="store_true", help="list the bucket's copies, upload nothing")
    args = ap.parse_args()

    settings = get_settings()
    if not configured(settings):
        print("offsite: not configured (FCP_S3_BUCKET and the AWS keys); nothing copied")
        return 0
    bucket = str(settings.fcp_s3_bucket)
    s3 = client(settings)

    if args.check:
        try:
            found = s3.list_objects_v2(Bucket=bucket, Prefix=settings.fcp_s3_prefix)
        except Exception as exc:
            print(f"offsite: could not list the bucket: {describe(exc)}")
            return 1
        rows = sorted(found.get("Contents", []), key=lambda o: o["LastModified"])
        for row in rows[-10:]:
            when = f"{row['LastModified']:%Y-%m-%d %H:%M}"
            print(f"offsite: {row['Key']}  {row['Size'] / 1e6:.1f} MB  {when}")
        print(
            f"offsite: {len(rows)} object(s) under {settings.fcp_s3_prefix} (at most 1000 listed)"
        )
        return 0

    dump = args.file or newest_dump(BACKUPS)
    if dump is None or not dump.exists():
        print("offsite: no dump to copy")
        return 1
    key = key_for(settings.fcp_s3_prefix, dump)
    try:
        copy(s3, bucket, key, dump)
    except Exception as exc:
        print(f"offsite: FAILED copying {dump.name}: {describe(exc)}")
        return 1
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(f"{dump.name} {datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ}\n")
    print(f"offsite: ok {dump.name} -> s3://{bucket}/{key} ({dump.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
