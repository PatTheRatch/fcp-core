"""The one thing to open on the night, and the defaults it works out."""

from pathlib import Path

import pytest

from scripts.draft_night import fill_defaults, newest_export
from scripts.draft_service import build_parser


def exports(folder: Path, *seasons: int, twin: bool = True) -> Path:
    for season in seasons:
        (folder / f"BBM_Projections_{season}_total.xls").write_bytes(b"")
        if twin:
            (folder / f"BBM_Projections_{season}_pergame.xls").write_bytes(b"")
    return folder


def test_the_newest_season_with_both_exports_is_the_one(tmp_path: Path) -> None:
    exports(tmp_path, 2026, 2027)
    exports(tmp_path, 2028, twin=False)

    found = newest_export(tmp_path)

    assert found is not None
    season, total, per_game = found
    assert season == 2027, "2028 has no per-game twin, so it is not a room"
    assert total.name == "BBM_Projections_2027_total.xls"
    assert per_game.name == "BBM_Projections_2027_pergame.xls"


def test_no_exports_is_none(tmp_path: Path) -> None:
    assert newest_export(tmp_path) is None
    assert newest_export(tmp_path / "missing") is None


def test_with_no_arguments_the_night_s_defaults_are_filled_in(tmp_path: Path) -> None:
    args = build_parser(require_room=False).parse_args([])

    filled = fill_defaults(args, exports=exports(tmp_path, 2027), tracked_team_id=10)

    assert filled.season == 2027
    assert filled.bbm == tmp_path / "BBM_Projections_2027_total.xls"
    assert filled.bbm_per_game == tmp_path / "BBM_Projections_2027_pergame.xls"
    assert filled.me == 10, "our team, by ESPN's id; the room resolves the name"
    assert filled.port == 8765 and filled.log is None, "the real log, on the usual port"


def test_every_default_gives_way_to_its_flag(tmp_path: Path) -> None:
    args = build_parser(require_room=False).parse_args(
        ["--season", "2026", "--me", "Brighton Bears", "--bbm", "x.xls", "--port", "9000"]
    )

    filled = fill_defaults(args, exports=exports(tmp_path, 2027), tracked_team_id=10)

    assert filled.season == 2026 and filled.me == "Brighton Bears"
    assert filled.bbm == Path("x.xls") and filled.port == 9000


def test_missing_exports_refuse_to_start_with_what_to_do(tmp_path: Path) -> None:
    args = build_parser(require_room=False).parse_args([])

    with pytest.raises(SystemExit, match=r"BBM_Projections_<season>_total\.xls"):
        fill_defaults(args, exports=tmp_path, tracked_team_id=10)


def test_no_team_anywhere_says_what_to_set(tmp_path: Path) -> None:
    args = build_parser(require_room=False).parse_args([])

    with pytest.raises(SystemExit, match="FCP_TRACKED_TEAM_ID"):
        fill_defaults(args, exports=exports(tmp_path, 2027), tracked_team_id=None)


def test_the_command_file_runs_the_launcher_from_the_repository() -> None:
    command = Path(__file__).parent.parent / "scripts" / "Draft Room.command"

    text = command.read_text()

    assert command.stat().st_mode & 0o111, "double-clickable, so executable"
    assert 'cd "$(dirname "$0")/.."' in text
    assert ".venv/bin/python scripts/draft_night.py" in text
    assert "read -r _" in text, "and the window stays open on a failure"
