"""The off-site copy: its key, its verification, and that it never leaks a key."""

from __future__ import annotations

import runpy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.watchdog import offsite_check

OFF = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts" / "offsite_backup.py"))

SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"


class FakeS3:
    def __init__(self, *, size_delta: int = 0, fail: Exception | None = None) -> None:
        self.size_delta, self.fail = size_delta, fail
        self.uploaded: list[tuple[str, str, str, dict[str, Any]]] = []

    def upload_file(self, path: str, bucket: str, key: str, ExtraArgs: dict[str, Any]) -> None:  # noqa: N803
        if self.fail:
            raise self.fail
        self.uploaded.append((path, bucket, key, ExtraArgs))

    def head_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        path = self.uploaded[-1][0]
        return {"ContentLength": Path(path).stat().st_size + self.size_delta}


def test_the_key_is_dated_from_the_dump() -> None:
    dump = Path("fcp-20260919T100026Z.dump")
    assert (
        OFF["key_for"]("fcp-core/backups/", dump)
        == "fcp-core/backups/2026/09/fcp-20260919T100026Z.dump"
    )
    assert OFF["key_for"]("x", Path("odd.dump")) == "x/undated/00/odd.dump"


def test_a_copy_is_encrypted_and_its_size_read_back(tmp_path: Path) -> None:
    dump = tmp_path / "fcp-20260919T100026Z.dump"
    dump.write_bytes(b"x" * 100)
    s3 = FakeS3()
    OFF["copy"](s3, "bucket", "k", dump)
    assert s3.uploaded[0][3] == {"ServerSideEncryption": "AES256"}


def test_a_short_copy_is_refused(tmp_path: Path) -> None:
    dump = tmp_path / "fcp-20260919T100026Z.dump"
    dump.write_bytes(b"x" * 100)
    with pytest.raises(RuntimeError):
        OFF["copy"](FakeS3(size_delta=-1), "bucket", "k", dump)


def test_an_error_is_reported_without_its_text() -> None:
    class ClientError(Exception):
        def __init__(self, text: str) -> None:
            super().__init__(text)
            self.response = {"Error": {"Code": "AccessDenied", "Message": SECRET}}

    said = OFF["describe"](ClientError(f"denied for {SECRET}"))
    assert said == "ClientError (AccessDenied)" and SECRET not in said


def _settings(bucket: str | None, key_id: str | None, secret: str | None) -> Settings:
    return Settings(
        database_url="postgresql+psycopg://x/y",
        test_database_url="postgresql+psycopg://x/z",
        fcp_s3_bucket=bucket,
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
    )


def test_unconfigured_means_nothing_to_do() -> None:
    assert OFF["configured"](_settings(None, None, None)) is False
    assert OFF["configured"](_settings("b", "AKIA", SECRET)) is True
    assert OFF["configured"](_settings("  ", "AKIA", SECRET)) is False


def test_the_watchdog_reads_the_marker(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    marker = tmp_path / "offsite-last-ok"
    assert offsite_check(marker, now).quiet is True
    marker.write_text("fcp-20260919T100026Z.dump 2026-09-19T10:01:00Z\n")
    fresh = offsite_check(marker, now)
    assert fresh.quiet is False and "fcp-20260919T100026Z.dump" in fresh.detail
    assert offsite_check(marker, now + timedelta(hours=40)).quiet is True
