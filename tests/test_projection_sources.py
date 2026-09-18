"""Naming the source of a projection, and the one gate that reads it.

The constraint is in docs/projection_sources.md: BBM's numbers are paid and
stay with the member who fetched them, ESPN's and an upload's do not. These
pin the default (nothing that existed before this claims to be anything but
ESPN's), which source is gated, and that `may_show` is the whole rule.
"""

from app.draft.valuation import PlayerProjection
from app.projections import sources

LINE = {"PTS": 1500.0, "REB": 400.0, "FGM": 500.0, "FGA": 1000.0}


def projection(name: str, source: str | None = None) -> PlayerProjection:
    extra = {} if source is None else {"source": source}
    return PlayerProjection(player_id=1, name=name, games=70.0, totals=dict(LINE), **extra)


def test_a_projection_nobody_tagged_is_espns() -> None:
    assert projection("Darius Garland").source == sources.ESPN


def test_only_basketball_monster_is_gated() -> None:
    assert sources.is_gated(sources.BBM)
    assert not sources.is_gated(sources.ESPN)
    assert not sources.is_gated(sources.upload_source(7))


def test_an_upload_tag_carries_the_set_it_came_from() -> None:
    assert sources.upload_source(7) == "upload:7"
    assert sources.upload_set_id("upload:7") == 7
    assert sources.upload_set_id(sources.BBM) is None
    assert sources.upload_set_id("upload:") is None


def test_a_gated_source_is_shown_only_to_the_account_that_supplied_it() -> None:
    assert sources.may_show(sources.BBM, viewer_owns_source=True) is True
    assert sources.may_show(sources.BBM, viewer_owns_source=False) is False
    assert sources.may_show(sources.ESPN, viewer_owns_source=False) is True
    assert sources.may_show(sources.upload_source(3), viewer_owns_source=False) is True


def test_a_pool_reports_every_source_it_was_built_from() -> None:
    pool = [
        projection("Evan Mobley"),
        projection("Cameron Boozer", sources.BBM),
        projection("AJ Dybantsa", sources.upload_source(2)),
    ]
    assert sources.sources_in(pool) == {"espn", "bbm", "upload:2"}
    assert sources.sources_in([]) == set()


def test_a_source_describes_itself_for_a_page() -> None:
    assert sources.describe(sources.ESPN) == "ESPN's projections"
    assert "not to be shared" in sources.describe(sources.BBM)
    assert sources.describe(sources.upload_source(4)) == "uploaded projection set 4"
