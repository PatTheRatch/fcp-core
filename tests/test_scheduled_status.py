"""The scheduled wrappers refuse to run against a database behind the code.

Runs the real shell script against the test database, downgraded by one
revision, and expects the refusal: exit 1 and a REFUSED line in the log.
The same guard is what keeps the nightly ingest honest, so it is checked
on both wrappers.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parent.parent
WRAPPERS = ("scheduled_status.sh", "scheduled_ingest.sh")


def _config(database_url: str) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.mark.parametrize("wrapper", WRAPPERS)
def test_the_wrapper_refuses_a_schema_behind_the_code(
    wrapper: str, test_database_url: str, tmp_path: Path
) -> None:
    config = _config(test_database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "-1")
    try:
        completed = subprocess.run(
            [str(REPO_ROOT / "scripts" / wrapper)],
            env={
                **os.environ,
                "DATABASE_URL": test_database_url,
                "FCP_PYTHON": sys.executable,
                "FCP_LOG_DIR": str(tmp_path),
                "FCP_DB_WAIT_SECONDS": "5",
            },
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    finally:
        command.upgrade(config, "head")

    assert completed.returncode == 1, completed.stderr
    [log] = list(tmp_path.glob("*.log"))
    assert "REFUSED: database schema is not at the latest migration" in log.read_text()


def test_the_nightly_ingest_also_takes_the_status_snapshot() -> None:
    script = (REPO_ROOT / "scripts" / "scheduled_ingest.sh").read_text()
    assert "scripts/status_pass.py --label nightly" in script
    assert 'scripts/ingest_league.py --recent "$RECENT_DAYS" --upcoming' in script


def test_the_morning_pass_sends_the_digest_and_the_others_only_alert() -> None:
    script = (REPO_ROOT / "scripts" / "scheduled_status.sh").read_text()
    assert '*morning*) DIGEST_ARGS="" ;;' in script
    assert '*) DIGEST_ARGS="--alert" ;;' in script
    assert "scripts/digest.py $DIGEST_ARGS" in script
    # The digest runs only once the pass itself has succeeded.
    assert script.index('status_pass.py "$@"') < script.index("digest.py $DIGEST_ARGS")


def test_the_timer_fires_at_the_three_slots() -> None:
    timer = (REPO_ROOT / "deploy" / "fcp-core-status.timer").read_text()
    for slot in ("15:00:00", "22:30:00", "00:30:00"):
        assert f"OnCalendar=*-*-* {slot} UTC" in timer
