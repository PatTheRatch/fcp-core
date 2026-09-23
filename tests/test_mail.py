"""The email: two parts, the house style, and a subject worth opening.

What is pinned: the text part is `Digest.render()` and nothing else, so what
`--dry-run` prints is what a reader in a terminal client gets; every section
a reader asked for is in the HTML and none he did not; a digest with nothing
to say renders its honest lines rather than a heading over blank space; the
HTML fetches nothing (no stylesheet, no script, no font, no image, no
tracking pixel) and uses none of the layout an email client cannot do; and
the subject follows its rule.

Nothing here opens a connection: a `Mail` is built, not sent.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from app import mail as mail_module
from app.digest import Digest, Standing
from app.inseason import changes as feed
from app.inseason.changes import Change, Person
from app.mail import alert_mail, confirm_mail, digest_mail, digest_subject, sign_in_mail
from app.notify import build_email
from app.subscriptions import (
    FULL,
    LEAGUE_TRANSACTIONS,
    LINEUP,
    MY_TEAM,
    STANDINGS,
    TOPICS,
    Subscription,
    everything,
)

NOW = datetime(2026, 1, 14, 9, 0, tzinfo=UTC)
SITE = "https://fcp.example"


def full() -> Subscription:
    """Every topic, at length. The default is the compact form now
    (docs/jobs.md, "The two forms"), so the long form is asked for by name
    in the tests that are about it."""
    return everything(FULL)


#: Anything that would make the client reach out for a file, and the layout
#: an email cannot do. `background:` is allowed (it is a colour here);
#: `background-image` is not.
FETCHES = (
    "<img",
    "<script",
    "<link",
    "background-image",
    "url(",
    "@import",
    "@font-face",
    "fonts.googleapis",
    "srcset",
)
#: What Outlook's Word engine does not lay out.
UNSUPPORTED = ("display:flex", "display:grid", "position:absolute", "position:fixed", "float:left")


def _digest(**changes: object) -> Digest:
    base: dict[str, object] = {
        "season": 2026,
        "team_name": "Through The Wire",
        "generated_at": NOW,
        "league_name": "Patriot Games",
        "topics": TOPICS,
    }
    base.update(changes)
    return Digest(**base)  # type: ignore[arg-type]


def _change(kind: str, text: str, *, mine: bool = False, opponent: bool = False) -> Change:
    return Change(
        at=NOW,
        kind=kind,
        players=(Person(espn_player_id=1, name="A Player"),),
        teams=(),
        text=text,
        mine=mine,
        opponent=opponent,
    )


# ---------------------------------------------------------------------------
# the two parts


def test_the_text_part_is_the_rendered_message_and_nothing_else() -> None:
    """It is what `--dry-run` prints and what the digest tests hold line for
    line; the HTML rides beside it, not over it."""
    digest = _digest()

    built = digest_mail(digest, wanted=full(), public_url=SITE)

    assert built.text == digest.render()
    assert built.html.startswith("<!doctype html>")


def test_the_league_section_is_appended_to_the_text_and_not_repeated_in_the_html() -> None:
    """The HTML has the same news under "What changed", filtered by his
    topics, so printing it twice would be printing it twice."""
    digest = _digest(feed=[_change(feed.ADD, "Somebody added A Player")])

    built = digest_mail(digest, wanted=full(), public_url=SITE, league_tail="THE LEAGUE\n  9 moves")

    assert built.text.endswith("THE LEAGUE\n  9 moves")
    assert "THE LEAGUE" not in built.html


def test_both_parts_ride_in_one_message() -> None:
    built = digest_mail(_digest(), wanted=full(), public_url=SITE)

    message = build_email(
        built.text,
        html=built.html,
        sender="fcp@example.net",
        recipients=["patrick@example.com"],
        subject=built.subject,
        headers=built.headers,
    )

    assert message.get_content_type() == "multipart/alternative"
    assert message["List-Unsubscribe"] == f"<{SITE}/account/alerts>"


# ---------------------------------------------------------------------------
# what an email client allows


def test_the_html_fetches_nothing_and_lays_out_with_tables() -> None:
    """No stylesheet, no script, no web font, no image -- not even a spacer,
    because a blocked image is a hole and a tracking pixel is a thing this
    product does not do."""
    html = digest_mail(_digest(), wanted=full(), public_url=SITE).html

    for forbidden in FETCHES:
        assert forbidden not in html.lower(), forbidden
    for forbidden in UNSUPPORTED:
        assert forbidden not in html.replace(" ", ""), forbidden
    assert "<table" in html, "an email lays out with tables"
    assert "max-width:600px" in html


def test_the_html_carries_no_custom_property_because_an_inbox_has_none() -> None:
    html = digest_mail(_digest(), wanted=full(), public_url=SITE).html

    assert "var(--" not in html
    assert "#C4551F" in html, "the site's accent, as a literal colour"


def test_every_face_falls_back_through_a_generic_family() -> None:
    """A client with none of the named faces still has one to use."""
    html = digest_mail(_digest(), wanted=full(), public_url=SITE).html

    stacks = re.findall(r'font-family:([^;"]+)', html)
    assert stacks
    for stack in stacks:
        last = stack.replace("\n", " ").strip().rsplit(",", 1)[-1].strip()
        assert last in {"serif", "sans-serif", "monospace"}, stack


def test_the_footer_carries_the_pages_it_came_from_and_a_way_to_manage_it() -> None:
    html = digest_mail(_digest(), wanted=full(), public_url=SITE).html

    assert f'href="{SITE}/account/alerts#wants"' in html
    assert "Manage your alerts" in html
    assert "You get this because" in html


def test_without_a_public_url_the_footer_says_so_rather_than_linking_nowhere() -> None:
    html = digest_mail(_digest(), wanted=full(), public_url=None).html

    assert "href=" not in html
    assert "No link is set on this server." in html


# ---------------------------------------------------------------------------
# the sections are the reader's, in the reader's order


def test_the_sections_are_exactly_the_topics_he_chose() -> None:
    digest = _digest(topics=(LINEUP, STANDINGS))
    only_two = Subscription(
        length=FULL, topics={**dict.fromkeys(TOPICS, False), LINEUP: True, STANDINGS: True}
    )

    html = digest_mail(digest, wanted=only_two, public_url=SITE).html

    assert "Today&#x27;s lineup" in html or "Today's lineup" in html
    assert "Standings and projections" in html
    assert "My moves worth a look" not in html
    assert "League transactions" not in html


def test_the_feed_topics_share_one_section_drawn_once() -> None:
    """My team, my opponent, league transactions, league injuries and trades
    are five ways into one feed, not five sections."""
    digest = _digest(
        feed=[_change(feed.DROP, "Somebody dropped A Player", mine=True)],
        topics=(MY_TEAM, LEAGUE_TRANSACTIONS),
    )
    both = Subscription(
        length=FULL,
        topics={**dict.fromkeys(TOPICS, False), MY_TEAM: True, LEAGUE_TRANSACTIONS: True},
    )

    html = digest_mail(digest, wanted=both, public_url=SITE).html

    assert html.count("Somebody dropped A Player") == 1
    assert html.count("What changed") == 1, "one section, not one per topic"
    assert "You asked for: my team&#x27;s transactions and injuries, league" in html


def test_a_line_he_did_not_ask_for_is_not_in_his_feed() -> None:
    mine = _change(feed.DROP, "My own man was dropped", mine=True)
    anyones = _change(feed.ADD, "Someone else added a man")
    digest = _digest(feed=[mine, anyones], topics=(MY_TEAM,))
    only_mine = Subscription(length=FULL, topics={**dict.fromkeys(TOPICS, False), MY_TEAM: True})

    html = digest_mail(digest, wanted=only_mine, public_url=SITE).html

    assert "My own man was dropped" in html
    assert "Someone else added a man" not in html


# ---------------------------------------------------------------------------
# a digest with nothing to say


def test_a_digest_with_nothing_to_say_renders_its_honest_lines_not_blanks() -> None:
    """A heading over blank space reads as a rendering fault. Every section
    says the line the text message would have said."""
    digest = _digest(
        today=["  no lineup today: nothing stored"],
        plan=["  no plan today: nothing stored"],
        season_plan=["  no season view today: nothing stored"],
        table=["  no matchup has been settled yet, so there is no table"],
    )

    html = digest_mail(digest, wanted=full(), public_url=SITE).html

    for words in (
        "no lineup today",
        "no plan today",
        "no season view today",
        "no matchup has been settled yet",
        "nothing you asked about changed",
    ):
        assert words in html, words
    assert '<td style="padding:26px 20px 0;">\n<div' in html, "the sections are still drawn"


def test_a_standing_shows_a_marked_slot_where_the_projected_finish_will_go() -> None:
    digest = _digest(
        place=Standing(place=3, of=12, won=6, lost=4, tied=1, categories_won=40, categories_lost=31)
    )

    html = digest_mail(digest, wanted=full(), public_url=SITE).html

    assert "3 of 12" in html
    assert "6-4-1" in html
    assert "not built yet" in html
    assert "The projected finish is not built yet" in html


# ---------------------------------------------------------------------------
# the compact form, which is what an email is unless he asks for the long one


def test_the_compact_form_is_the_default_and_draws_its_own_four_sections() -> None:
    """Tonight, Worth a look, Since yesterday, Standing -- and none of the
    long form's headings. `everything()` is compact: the default is a
    default everywhere, including a preview."""
    html = digest_mail(_digest(), wanted=everything(), public_url=SITE).html

    for heading in ("Tonight", "Worth a look", "Since yesterday", "Standing"):
        assert f">{heading}</span>" in html, heading
    for long_form in ("Today&#x27;s lineup", "This week</span>", "The season", "What changed"):
        assert long_form not in html, long_form


def test_a_compact_section_he_did_not_ask_for_is_not_drawn() -> None:
    only_tonight = Subscription(topics={**dict.fromkeys(TOPICS, False), LINEUP: True})

    html = digest_mail(_digest(topics=(LINEUP,)), wanted=only_tonight, public_url=SITE).html

    assert ">Tonight</span>" in html
    for heading in ("Worth a look", "Since yesterday", "Standing"):
        assert f">{heading}</span>" not in html, heading


def test_the_compact_feed_is_counts_with_the_injury_said_in_full() -> None:
    """The one kind of news worth the space is a status change on a roster
    that matters to him; a dollar claim by somebody else is a count."""
    digest = _digest(
        feed=[
            _change(feed.STATUS, "Kawhi Leonard is out: ACTIVE to OUT.", mine=True),
            _change(feed.CLAIM, "Load Management claimed A Player for $1."),
            _change(feed.CLAIM, "Another Team claimed B Player for $1."),
            _change(feed.DROP, "A Rival dropped C Player.", opponent=True),
        ],
        espn_league_id=3853870,
        espn_team_id=1,
    )

    html = digest_mail(digest, wanted=everything(), public_url=SITE).html

    assert "Kawhi Leonard is out: ACTIVE to OUT." in html
    assert "claimed A Player" not in html, "a claim is a count, not a sentence"
    assert "1 on your roster" in html
    assert "1 on your opponent&#x27;s" in html
    assert "2 around the league" in html
    assert f'href="{SITE}/l/3853870/2026/week#changed"' in html


def test_the_compact_form_names_the_pages_without_linking_when_it_cannot() -> None:
    """A digest that does not know its league -- one built by hand, for a
    preview -- prints the words rather than a link that goes nowhere."""
    html = digest_mail(
        _digest(feed=[_change(feed.STATUS, "Somebody is out.", mine=True)]),
        wanted=everything(),
        public_url=SITE,
    ).html

    assert "1 on your roster" in html
    assert f'href="{SITE}/l/' not in html


def test_the_compact_standing_is_one_line_with_the_marked_slot_still_in_it() -> None:
    digest = _digest(
        place=Standing(place=3, of=12, won=6, lost=4, tied=1, categories_won=40, categories_lost=31)
    )

    html = digest_mail(digest, wanted=everything(), public_url=SITE).html

    assert "3 of 12, 6-4-1 on matchups" in html
    assert "40-31 on categories" in html
    assert "projected finish: not built yet" in html


def test_the_compact_form_fetches_nothing_either() -> None:
    html = digest_mail(_digest(), wanted=everything(), public_url=SITE).html

    for forbidden in FETCHES:
        assert forbidden not in html.lower(), forbidden
    for forbidden in UNSUPPORTED:
        assert forbidden not in html.replace(" ", ""), forbidden


# ---------------------------------------------------------------------------
# the subject


def test_the_subject_is_the_team_and_the_one_thing_worth_opening_it_for() -> None:
    """Eight words at most, because a phone shows about forty characters of
    a subject and that is where a manager decides."""
    assert digest_subject(_digest()) == "Through The Wire: nothing to fix today"

    long_one = mail_module._eight("one two three four five six seven eight nine ten")
    assert long_one == "one two three four five six seven eight…"


def test_the_alerts_subject_is_its_first_line() -> None:
    built = alert_mail(
        "Through The Wire",
        "Kawhi Leonard: ACTIVE to OUT, back 01 Dec\nAnother Man: ACTIVE to OUT",
        when="Tue 03 Nov, 22:30 UTC",
        public_url=SITE,
    )

    assert built.subject.startswith("Through The Wire: Kawhi Leonard: ACTIVE to OUT,")
    assert "Kawhi Leonard" in built.html and "Kawhi Leonard" in built.text
    for forbidden in FETCHES:
        assert forbidden not in built.html.lower(), forbidden


# ---------------------------------------------------------------------------
# the account mail


def test_the_sign_in_mail_has_both_parts_and_the_link_twice() -> None:
    """As a button and as a plain URL under it: a client that strips the
    link, a forwarded copy and a reader who does not trust a button all need
    the address itself."""
    link = f"{SITE}/auth/callback?token=abc123"

    built = sign_in_mail(link, public_url=SITE)

    assert built.subject == "Your FCP sign-in link"
    assert link in built.text
    assert built.html.count(link) == 2, "the button's href, and the URL under it"
    assert "Sign in" in built.html
    for forbidden in FETCHES:
        assert forbidden not in built.html.lower(), forbidden
    assert "List-Unsubscribe" not in built.headers, "a sign-in link is not a subscription"


def test_the_confirmation_mail_is_the_same_shape() -> None:
    link = f"{SITE}/account/alerts?token=abc123"

    built = confirm_mail(link, public_url=SITE)

    assert built.subject == "Confirm this address for FCP alerts"
    assert link in built.text and built.html.count(link) == 2
    assert built.headers["List-Unsubscribe"] == f"<{SITE}/account/alerts>"
