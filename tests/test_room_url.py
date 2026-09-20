"""The draft room's URL, built rather than pasted."""

from app.draft.room_url import draft_room_url

SWID = "0A1B2C3D-4E5F-6A7B-8C9D-0E1F2A3B4C5D"
EXPECTED = (
    "https://fantasy.espn.com/basketball/draft"
    "?leagueId=12345&seasonId=2027&teamId=10&memberId={" + SWID + "}"
)


def test_the_league_s_own_room_is_built_from_its_four_parts() -> None:
    assert draft_room_url(12345, 2027, 10, "{" + SWID + "}") == EXPECTED


def test_a_swid_kept_without_its_braces_gets_them_back() -> None:
    """ESPN writes the cookie in braces and wants the member id the same way;
    a `.env` that dropped them, or lower-cased it, is still the same person."""
    assert draft_room_url(12345, 2027, 10, SWID) == EXPECTED
    assert draft_room_url(12345, 2027, 10, SWID.lower()) == EXPECTED


def test_a_missing_part_is_no_url_rather_than_half_of_one() -> None:
    assert draft_room_url(None, 2027, 10, SWID) is None
    assert draft_room_url(12345, None, 10, SWID) is None
    assert draft_room_url(12345, 2027, None, SWID) is None
    assert draft_room_url(12345, 2027, 10, None) is None
    assert draft_room_url(12345, 2027, 10, "") is None
    assert draft_room_url(12345, 2027, 10, "not a swid") is None, "and neither is a bad one"
