"""Reading a manager's own projections out of whatever file he has.

The upload path is what lets anyone who does not pay Basketball Monster use
the room at all (docs/projection_sources.md), so these pin the parts that
decide whether a board built on an upload is trustworthy: the column guess on
headers nobody standardised, the refusal of a file that carries a percentage
and no attempts, the per-game-or-totals question answered from the numbers
rather than asked, and a round trip that lands a matched player under his ESPN
id and an unmatched rookie under a synthetic one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import PlayerSeasonStat, ProjectionRow, ProjectionSet
from app.player_names import synthetic_id
from app.projections import sources
from app.projections.upload import (
    detect_basis,
    guess_mapping,
    import_set,
    load_projection_set,
    read_table,
    set_headline,
    set_note,
    stored_sets,
)
from tests.scoring_db import player

SEASON = 2027

#: A per-game file with the columns spelled the way one real site spells them.
PER_GAME = """Player,Team,Pos,GP,PTS,REB,AST,STL,BLK,3PM,TO,FGM,FGA,FTM,FTA
Evan Mobley,CLE,PF,70,18.5,9.3,3.2,0.9,1.6,1.1,2.1,7.2,13.4,3.0,4.1
Cameron Boozer,CHA,PF/C,65,14.0,7.5,2.0,0.8,0.9,0.7,1.8,5.5,11.0,2.5,3.4
Darius Garland,CLE,PG,62,20.6,2.9,6.7,1.2,0.1,2.8,2.6,7.4,16.0,3.0,3.4
Evan Mobley,CLE,PF,70,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,2.0,1.0,2.0
"""


#: The columns a Basketball Monster export spells its per-game rates with,
#: beside two of its derived value columns.
BBM_SHAPED = [
    *("Name", "Pos", "g", "p/g", "r/g", "a/g", "s/g", "b/g", "3/g", "to/g"),
    *("toV", "pV", "fg%", "fga/g", "ft%", "fta/g"),
]


def write(tmp_path: Path, body: str, name: str = "projections.csv") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def league_players(session: Session) -> None:
    """Two players ESPN knows, one with an eligible line, and no rookie."""
    mobley = player(session, "Evan Mobley")
    garland = player(session, "Darius Garland")
    session.add(
        PlayerSeasonStat(
            player_id=mobley.id,
            season=SEASON,
            kind="projected",
            games_played=70.0,
            raw_totals={},
            eligible_slots=["PF", "C", "F", "UT"],
            primary_position="PF",
        )
    )
    session.add(
        PlayerSeasonStat(
            player_id=garland.id,
            season=SEASON,
            kind="projected",
            games_played=62.0,
            raw_totals={},
            eligible_slots=["PG", "G", "UT"],
            primary_position="PG",
        )
    )
    session.flush()


# ---------------------------------------------------------------------------
# the column guess
# ---------------------------------------------------------------------------


def test_headers_nobody_standardised_are_mapped_by_their_synonyms() -> None:
    mapping = guess_mapping(
        [
            "PLAYER NAME",
            "Tm",
            "Pos.",
            "G",
            "P/G",
            "R/G",
            "A/G",
            "S/G",
            "B/G",
            "3/G",
            "TO/G",
            "FGM",
            "FGA",
            "FTM",
            "FTA",
            "Min",
            "ADP",
        ]
    )

    assert mapping.usable
    assert mapping.fields["name"] == "PLAYER NAME"
    assert mapping.fields["games"] == "G"
    assert mapping.fields["PTS"] == "P/G"
    assert mapping.fields["3PM"] == "3/G"
    assert mapping.fields["TO"] == "TO/G"
    assert mapping.fields["team"] == "Tm"
    assert mapping.fields["position"] == "Pos."
    # Minutes are an optional field since 2026-09-26; ADP is nothing of ours.
    assert mapping.fields["minutes"] == "Min"
    assert mapping.ignored == ("ADP",)


def test_makes_are_rebuilt_from_a_percentage_and_its_attempts() -> None:
    mapping = guess_mapping(
        [
            "Player",
            "GP",
            "PTS",
            "REB",
            "AST",
            "STL",
            "BLK",
            "3PM",
            "TOV",
            "FG%",
            "FGA",
            "FT%",
            "FTA",
        ]
    )

    assert mapping.usable
    assert mapping.derived["FGM"] == ("FG%", "FGA")
    assert mapping.derived["FTM"] == ("FT%", "FTA")


def test_a_value_column_does_not_steal_the_count_it_is_named_after() -> None:
    """BBM's export carries `to/g` and `toV`, its derived turnover value."""
    mapping = guess_mapping(BBM_SHAPED)

    assert mapping.usable
    assert mapping.fields["TO"] == "to/g"
    assert "toV" in mapping.ignored


def test_a_percentage_without_attempts_is_refused_with_the_reason() -> None:
    mapping = guess_mapping(
        ["Player", "GP", "PTS", "REB", "AST", "STL", "BLK", "3PM", "TOV", "FG%", "FT%"]
    )

    assert not mapping.usable
    assert "FGA" in mapping.missing and "FGM" in mapping.missing
    said = " ".join(mapping.problems)
    assert "a percentage cannot be rebuilt" in said
    assert "app/scoring/lines.py" in said


def test_a_file_with_no_games_column_is_refused() -> None:
    mapping = guess_mapping(
        ["Player", "PTS", "REB", "AST", "STL", "BLK", "3PM", "TOV", "FGM", "FGA", "FTM", "FTA"]
    )

    assert not mapping.usable
    assert mapping.missing == ("games",)


def test_a_map_override_beats_the_guess() -> None:
    headers = ["Player", "GP", "Scoring", "REB", "AST", "STL", "BLK", "3PM", "TOV"]
    headers += ["FGM", "FGA", "FTM", "FTA"]

    guessed = guess_mapping(headers)
    assert not guessed.usable

    mapped = guess_mapping(headers, overrides={"Scoring": "PTS"})
    assert mapped.usable
    assert mapped.fields["PTS"] == "Scoring"


def test_an_override_naming_a_column_the_file_has_not_got_is_a_problem() -> None:
    mapping = guess_mapping(["Player", "GP", "PTS"], overrides={"Nope": "REB"})

    assert not mapping.usable
    assert any("no column called 'Nope'" in problem for problem in mapping.problems)


# ---------------------------------------------------------------------------
# per game or season totals
# ---------------------------------------------------------------------------


def test_the_basis_is_measured_from_the_points_column_not_asked_for(tmp_path: Path) -> None:
    headers, rows = read_table(write(tmp_path, PER_GAME))
    mapping = guess_mapping(headers)

    assert detect_basis(rows, mapping) == "per_game"

    totals = PER_GAME.replace("18.5", "1295").replace("20.6", "1277").replace("14.0", "910")
    _, total_rows = read_table(write(tmp_path, totals, "totals.csv"))
    assert detect_basis(total_rows, mapping) == "totals"


def test_a_totals_file_comes_back_as_the_same_totals(
    scoring_session: Session, tmp_path: Path
) -> None:
    league_players(scoring_session)
    body = (
        "Player,GP,PTS,REB,AST,STL,BLK,3PM,TO,FGM,FGA,FTM,FTA\n"
        "Evan Mobley,70,1295,651,224,63,112,77,147,504,938,210,287\n"
        "Darius Garland,62,1277,180,415,74,6,174,161,459,992,186,211\n"
    )
    report = import_set(
        scoring_session,
        season=SEASON,
        name="totals",
        owner="patrick",
        path=write(tmp_path, body, "totals.csv"),
    )

    assert report.basis == "totals"
    assert report.set_id is not None
    projections = {p.name: p for p in load_projection_set(scoring_session, report.set_id)}
    assert projections["Evan Mobley"].get("PTS") == pytest.approx(1295.0)
    assert projections["Evan Mobley"].get("FGA") == pytest.approx(938.0)


# ---------------------------------------------------------------------------
# a round trip
# ---------------------------------------------------------------------------


def test_a_csv_round_trip_places_the_known_and_keeps_the_rookie(
    scoring_session: Session, tmp_path: Path
) -> None:
    league_players(scoring_session)
    report = import_set(
        scoring_session,
        season=SEASON,
        name="preseason",
        owner="patrick",
        source_note="from a site I pay for",
        path=write(tmp_path, PER_GAME),
    )

    assert report.ok
    assert report.rows_read == 4
    assert report.rows_stored == 3
    assert report.matched == 2
    assert report.unmatched == ("Cameron Boozer",)
    assert report.duplicates == ("Evan Mobley",)
    assert report.rejected == ()
    assert report.set_id is not None

    projections = {p.name: p for p in load_projection_set(scoring_session, report.set_id)}
    assert set(projections) == {"Evan Mobley", "Cameron Boozer", "Darius Garland"}

    mobley = projections["Evan Mobley"]
    assert mobley.player_id > 0
    assert mobley.get("PTS") == pytest.approx(18.5 * 70)
    assert mobley.get("FGM") == pytest.approx(7.2 * 70)
    assert mobley.eligible == frozenset({"PF", "C", "F", "UT"})
    assert mobley.source == sources.upload_source(report.set_id)

    # Nobody of ours is called Cameron Boozer, so he goes on the board under a
    # synthetic id, with slots derived from the file's own position string.
    boozer = projections["Cameron Boozer"]
    assert boozer.player_id == synthetic_id("Cameron Boozer")
    assert boozer.eligible == frozenset({"PF", "C", "F", "UT"})
    assert boozer.position == "PF"


def test_a_percentage_file_round_trips_into_makes(scoring_session: Session, tmp_path: Path) -> None:
    league_players(scoring_session)
    body = (
        "Player,GP,PTS,REB,AST,STL,BLK,3PM,TOV,FG%,FGA,FT%,FTA\n"
        "Evan Mobley,70,18.5,9.3,3.2,0.9,1.6,1.1,2.1,53.7%,13.4,0.732,4.1\n"
    )
    report = import_set(
        scoring_session,
        season=SEASON,
        name="rates",
        owner="patrick",
        path=write(tmp_path, body, "rates.csv"),
    )

    assert report.ok and report.set_id is not None
    mobley = load_projection_set(scoring_session, report.set_id)[0]
    assert mobley.get("FGM") == pytest.approx(0.537 * 13.4 * 70)
    assert mobley.get("FTM") == pytest.approx(0.732 * 4.1 * 70)


def test_a_row_without_games_is_rejected_and_named(
    scoring_session: Session, tmp_path: Path
) -> None:
    league_players(scoring_session)
    body = PER_GAME.replace("Darius Garland,CLE,PG,62", "Darius Garland,CLE,PG,")
    body += "Broken Row,CLE,PG,70,many,1,1,1,1,1,1,1,2,1,2\n"
    report = import_set(
        scoring_session,
        season=SEASON,
        name="messy",
        owner="patrick",
        path=write(tmp_path, body, "messy.csv"),
    )

    assert report.rows_stored == 2
    said = dict(report.rejected).values()
    assert any("Darius Garland" in why and "games" in why for why in said)
    assert any("Broken Row" in why and "not a number" in why for why in said)


def test_a_dry_run_reads_everything_and_stores_nothing(
    scoring_session: Session, tmp_path: Path
) -> None:
    league_players(scoring_session)
    report = import_set(
        scoring_session,
        season=SEASON,
        name="preseason",
        owner="patrick",
        path=write(tmp_path, PER_GAME),
        dry_run=True,
    )

    assert report.dry_run and report.set_id is None
    assert report.rows_stored == 3
    assert scoring_session.scalar(select(func.count()).select_from(ProjectionSet)) == 0
    assert scoring_session.scalar(select(func.count()).select_from(ProjectionRow)) == 0
    assert "Re-run with --commit" in "\n".join(report.lines())


def test_a_refused_file_stores_nothing_and_says_why(
    scoring_session: Session, tmp_path: Path
) -> None:
    body = (
        "Player,GP,PTS,REB,AST,STL,BLK,3PM,TOV,FG%,FT%\nEvan Mobley,70,18.5,9,3,1,2,1,2,.54,.73\n"
    )
    report = import_set(
        scoring_session,
        season=SEASON,
        name="no attempts",
        owner="patrick",
        path=write(tmp_path, body, "rates_only.csv"),
    )

    assert not report.ok
    assert report.rows_stored == 0
    assert report.set_id is None
    assert scoring_session.scalar(select(func.count()).select_from(ProjectionSet)) == 0
    assert "refused, nothing stored:" in report.lines()


def test_a_stored_set_is_listed_and_describes_itself(
    scoring_session: Session, tmp_path: Path
) -> None:
    league_players(scoring_session)
    report = import_set(
        scoring_session,
        season=SEASON,
        name="preseason",
        owner="patrick",
        source_note="from a site I pay for",
        path=write(tmp_path, PER_GAME),
    )
    assert report.set_id is not None

    listed = stored_sets(scoring_session, SEASON)
    assert [(s.id, s.name, s.rows) for s in listed] == [(report.set_id, "preseason", 3)]
    assert listed[0].column_map["fields"]["PTS"] == "PTS"
    assert listed[0].column_map["basis"] == "per_game"

    note = set_note(scoring_session, report.set_id)
    assert "uploaded set" in note and "2 matched to ESPN ids" in note
    assert "from a site I pay for" in note

    # The header wants the name and the note, and not the matching counts.
    assert set_headline(scoring_session, report.set_id) == "'preseason': from a site I pay for"


# ---------------------------------------------------------------------------
# the other two file formats
# ---------------------------------------------------------------------------


def test_an_xlsx_is_read_like_a_csv(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    for line in PER_GAME.strip().splitlines():
        sheet.append([_cell(value) for value in line.split(",")])
    path = tmp_path / "projections.xlsx"
    book.save(str(path))

    headers, rows = read_table(path)
    assert headers[0] == "Player"
    assert len(rows) == 4
    assert rows[0]["PTS"] == 18.5
    assert guess_mapping(headers).usable


def test_a_legacy_xls_export_is_read_if_one_is_on_this_machine() -> None:
    export = Path("data/bbm/BBM_Projections_2027_total.xls")
    if not export.exists():
        pytest.skip("no .xls on this machine")

    headers, rows = read_table(export)
    assert headers and rows
    assert len(rows[0]) == len(headers)


def _cell(value: str) -> float | str:
    try:
        return float(value)
    except ValueError:
        return value


def test_a_room_is_drafted_on_one_pool_not_two() -> None:
    """Refused before anything is read, so no database is touched."""
    from app.draft.live import RoomError, load_room

    with pytest.raises(RoomError, match="one pool"):
        load_room(
            SEASON,
            "Through The Wire",
            pool_season=None,
            pool_kind="projected",
            punt=[],
            restarts=1,
            bbm=Path("data/bbm/whatever.xls"),
            projection_set=1,
        )


def test_an_unknown_extension_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path, PER_GAME, "projections.json")
    with pytest.raises(ValueError, match="not a projection file"):
        read_table(path)
