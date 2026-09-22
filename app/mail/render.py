"""The digest as a page: one HTML template, built from the `Digest`.

The message a manager reads at breakfast is a page, not a wall of text. This
builds it: the masthead, then the sections he subscribed to in the order
`app.subscriptions` lists them, then a footer that says why he got it.

WHAT AN EMAIL ALLOWS

Tables for layout, inline styles, literal colours, system fonts, and nothing
else. No stylesheet (a client will not fetch one), no `<style>` block worth
relying on (Outlook's Word engine throws most of it away), no script, no web
font, no image -- not even a spacer, because a blocked image is a hole and a
tracking pixel is a thing this product does not do. No flexbox, no grid, no
`position`, no float: Word lays out the Outlook client and knows none of
them. `app/mail/style.py` is the palette and the faces.

Two habits are Outlook's in particular. **A colour is set twice**, as the
`bgcolor` attribute and as `background:`, because Word paints the attribute
and ignores the property on a table. And the column is **`width="600"` as an
attribute beside `width:100%;max-width:600px` as a style**: Word has no
`max-width`, so it takes the attribute and draws 600; every other client
takes the style and shrinks to the phone. A declaration a client does not
know is dropped rather than failing, which is why `font-variant-numeric` and
`letter-spacing` can be asked for at no cost.

WHAT THE SECTIONS ARE

**Today** is the one thing that expires: the place going empty tonight, in
the warn colour, and the lineup as a small grid of slot, man and his game.
**This week** and **The season** are the moves, each with its number and one
line of reason; a move that did not clear the bar is shown and labelled
"under the bar", because a bar labels and never hides. **What changed** is
the feed's own sentences, grouped by day and filtered by his topics.
**Standings** is where he is, with a marked slot where the projected finish
will go.

Every section says something. A section with nothing in it says so in the
honest line the text message uses -- "nothing new", "no plan today" -- rather
than leaving a heading over blank space, which reads as a rendering fault.

THE LANGUAGE is the pages': **worth a look** and **nothing clears the bar**,
never *recommended* and never *do this* (docs/in_season_pages.md).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from html import escape

from app.digest import Digest
from app.inseason.changes import Change
from app.mail import style as s
from app.pickups.season import SeasonReport, Swap
from app.pickups.stream import Move, StreamReport
from app.pickups.today import ON_IR, Misstart, TodayReport
from app.subscriptions import (
    LABELS,
    LINEUP,
    MOVES,
    STANDINGS,
    Subscription,
)

#: The nine, in the order every screen of this product uses them.
NINE = ("FG%", "FT%", "3PM", "PTS", "REB", "AST", "STL", "BLK", "TO")

#: Men named on a list before the rest are counted, as the text message caps
#: its own lines. A page can hold more than a phone message, and not much.
FEED_LIMIT = 24
FIX_LIMIT = 3
BENCH_LIMIT = 5
MOVE_LIMIT = 3
#: Moves that did not clear the bar, shown and labelled. Two: the bar has to
#: label rather than hide, and a list of near misses is not a plan.
UNDER_LIMIT = 2


def _h(value: object) -> str:
    return escape(str(value), quote=True)


# ---------------------------------------------------------------------------
# the frame
# ---------------------------------------------------------------------------


def _open(title: str, preview: str) -> str:
    """The document, the outer table, and the one-line preview text.

    The preview is what an inbox shows beside the subject. It is a real
    element held off-screen by its own colour and size rather than by a
    class, because a class needs a stylesheet.
    """
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<title>{_h(title)}</title>
</head>
<body style="margin:0;padding:0;background:{s.GROUND};">
<div style="display:none;font-size:1px;color:{s.GROUND};line-height:1px;max-height:0;\
max-width:0;opacity:0;overflow:hidden;">{_h(preview)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
  bgcolor="{s.GROUND}" style="background:{s.GROUND};">
<tr><td align="center" style="padding:0 16px;">
<table role="presentation" width="{s.WIDTH}" cellpadding="0" cellspacing="0" border="0"
  bgcolor="{s.SURFACE}" style="width:100%;max-width:{s.WIDTH}px;background:{s.SURFACE};">
"""


def _close() -> str:
    return "</table>\n</td></tr>\n</table>\n</body>\n</html>\n"


def _masthead(eyebrow: str, title: str, sub: str = "") -> str:
    """The season report's masthead: an eyebrow, the name, a hard rule.

    The sub is left out when there is nothing to say under the name, rather
    than drawn empty: the account mail's masthead is two words and a rule.
    """
    line = f'<p style="margin:8px 0 0;{s.NOTE}">{_h(sub)}</p>' if sub else ""
    return f"""<tr><td style="padding:28px 20px 16px;border-bottom:3px solid {s.RULE_STRONG};">
<p style="margin:0;{s.EYEBROW}">{_h(eyebrow)}</p>
<h1 style="margin:8px 0 0;font-family:{s.DISPLAY};font-size:32px;\
line-height:1.05;font-weight:bold;text-transform:uppercase;color:{s.INK};">{_h(title)}</h1>
{line}
</td></tr>
"""


def _section(title: str, body: str) -> str:
    """One section: a heading with a rule under it, then its body.

    No tag beside the heading, as the pages have: a tag is pushed right with
    a float, and Outlook's Word engine does not float.
    """
    return f"""<tr><td style="padding:26px 20px 0;">
<div style="border-bottom:1px solid {s.RULE_STRONG};padding-bottom:6px;">
<span style="{s.HEAD}">{_h(title)}</span>
</div>
{body}
</td></tr>
"""


def _empty(words: str) -> str:
    """What a section with nothing in it says. Never blank space."""
    return f'<p style="margin:12px 0 0;{s.NOTE}font-style:italic;">{_h(words)}</p>'


def _lede(words: str) -> str:
    return f'<p style="margin:10px 0 0;{s.NOTE}">{_h(words)}</p>'


# ---------------------------------------------------------------------------
# Today
# ---------------------------------------------------------------------------


def _fix_line(misstart: Misstart) -> str:
    """The place that will produce nothing tonight, in the warn colour.

    The one thing in the message that expires: it is worth fixing before
    tip-off and worth nothing after it.
    """
    where = (
        f"{misstart.seat.slot} is empty"
        if misstart.seat.player is None
        else f"{misstart.seat.player.name} has no game at {misstart.seat.slot}"
    )
    instead = ", ".join(player.name for player in misstart.instead[:2])
    tail = f" &#8212; {_h(instead)} could take it" if instead else ""
    return f'<p style="margin:10px 0 0;{s.TEXT}color:{s.WARN};"><b>Fix:</b> {_h(where)}{tail}</p>'


def _today(report: TodayReport | None, lines: Sequence[str]) -> str:
    """The day's lineup: what to fix, then the grid of who starts where."""
    if report is None:
        return "".join(_empty(line.strip()) for line in lines) or _empty("no lineup today")
    when = f"{report.calendar_date:%a %d %b}" if report.calendar_date is not None else ""
    if report.teams_playing == 0:
        return _empty(f"day {report.today}{', ' + when if when else ''}: no NBA games tonight")

    out = [
        _lede(
            f"Day {report.today}{', ' + when if when else ''}: "
            f"{report.starts} of {len(report.lineup)} places fillable"
            + (f", {report.actual_starts} set" if report.actual_known else "")
        )
    ]
    for misstart in report.fix[:FIX_LIMIT]:
        out.append(_fix_line(misstart))
    hidden = len(report.fix) - FIX_LIMIT
    if hidden > 0:
        out.append(_lede(f"and {hidden} more place(s) worth fixing"))
    if not report.fix:
        out.append(
            f'<p style="margin:10px 0 0;{s.TEXT}color:{s.GOOD};">'
            "<b>Nothing to fix:</b> every place that can produce tonight is filled.</p>"
        )

    rows = [
        f'<tr><td style="{s.CELL_TOP}{s.TAG}width:56px;">Place</td>'
        f'<td style="{s.CELL_TOP}{s.TEXT}">Who</td>'
        f'<td style="{s.CELL_TOP}{s.NUM}color:{s.FAINT};" align="right">Game</td></tr>'
    ]
    for seat in report.lineup:
        if seat.player is None:
            # The empty places are counted under the grid rather than given a
            # row each: a roster short at three UT slots printed the same
            # sentence three times, which reads as a fault rather than a fact.
            continue
        game = seat.player.game.describe() if seat.player.game is not None else "no game"
        flag = (
            f' <span style="{s.TAG}color:{s.WARN};">{_h(seat.player.status)}</span>'
            if seat.player.status != "healthy"
            else ""
        )
        rows.append(
            f'<tr><td style="{s.CELL}{s.TAG}">{_h(seat.slot)}</td>'
            f'<td style="{s.CELL}{s.TEXT}"><b>{_h(seat.player.name)}</b>{flag}</td>'
            f'<td style="{s.CELL}{s.NUM}color:{s.MUTED};" align="right">{_h(game)}</td></tr>'
        )
    out.append(
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"'
        ' style="margin-top:14px;border-collapse:collapse;">' + "".join(rows) + "</table>"
    )
    if report.empty_slots:
        out.append(
            _lede(
                f"Nobody on the roster can fill {', '.join(report.empty_slots)} today: "
                "the roster is short there, which is not a mistake in tonight's lineup."
            )
        )

    benched = [b.player.name for b in report.benched[:BENCH_LIMIT]]
    if benched:
        out.append(_lede("A game and no place: " + ", ".join(benched)))
    idle = [player.name for player in report.idle[:BENCH_LIMIT]]
    if idle:
        extra = len(report.idle) - BENCH_LIMIT
        out.append(
            _lede(
                "Sitting, no game: " + ", ".join(idle) + (f" and {extra} more" if extra > 0 else "")
            )
        )
    on_ir = [player.name for player in report.injured_reserve]
    if on_ir:
        out.append(_lede(f"On {ON_IR.upper()}: " + ", ".join(on_ir)))
    return "".join(out)


# ---------------------------------------------------------------------------
# This week, and the season
# ---------------------------------------------------------------------------


def _move_row(headline: str, net: float, reason: str, mark: str) -> str:
    """One move: what it is, what it is worth, one line of why, and the bar's
    own word on it. The number and the reason are always both there: a
    number with no reason is a verdict."""
    tag = (
        f'<span style="{s.TAG}color:{s.ACCENT};border:1px solid {s.ACCENT};'
        f'padding:1px 6px;">{_h(mark)}</span>'
        if mark == "clears the bar"
        else f'<span style="{s.TAG}border:1px solid {s.RULE};padding:1px 6px;">{_h(mark)}</span>'
    )
    return f"""<tr><td style="padding:14px 0 10px;border-top:1px solid {s.RULE};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td style="{s.TEXT}font-size:17px;font-weight:bold;">{_h(headline)}</td>
<td align="right" valign="top" style="{s.NUM}font-size:17px;white-space:nowrap;\
padding-left:14px;">{s.gain(net)}</td></tr>
</table>
<p style="margin:5px 0 0;{s.NOTE}">{_h(reason)}</p>
<p style="margin:6px 0 0;">{tag}</p>
</td></tr>
"""


def _side(move: Move, today: int) -> str:
    """One streaming move in the recommender's own words."""
    add = move.add
    coming = f"{add.name} ({move.add_starts} of {add.games_remaining_this_period} games)"
    if not add.seatable_on(today) and add.waiver_clears_at is not None:
        coming += f", on waivers, clears {add.waiver_clears_at:%a}"
    if move.to_ir is not None:
        return f"Add {coming}, {move.to_ir.name} to IR"
    if move.drop is not None:
        return f"Add {coming}, drop {move.drop.name}"
    return f"Add {coming} into the open place"


def _why(move: Move) -> str:
    j = move.judgement
    moved = sorted(move.shifts, key=lambda shift: -abs(shift.delta))[:3]
    cats = ", ".join(f"{shift.abbreviation} {shift.delta:+.2f}" for shift in moved if shift.delta)
    week = f"week {j.delta_week:+.2f}, season {j.delta_season_per_week:+.2f} a week"
    record = (
        f"record {j.record_without[0]:.1f}-{j.record_without[1]:.1f} without, "
        f"{j.record_with[0]:.1f}-{j.record_with[1]:.1f} with"
    )
    return f"{week}; {record}" + (f". Mostly {cats}." if cats else "")


def _week(report: StreamReport | None, opponent: str | None, lines: Sequence[str]) -> str:
    if report is None:
        return "".join(_empty(line.strip()) for line in lines) or _empty("no plan today")
    first, last = report.scoring_periods_remaining[0], report.scoring_periods_remaining[-1]
    if report.on_bye:
        return _empty(
            f"Period {report.matchup_period}, days {first}-{last} left: on a bye, "
            "so there is no week to plan for"
        )
    out = [
        _lede(
            f"Period {report.matchup_period} against {opponent or report.opponent_team_id}. "
            f"Days {first}-{last} left. {report.expected_wins:.2f} of 9 categories as things "
            f"stand; {report.adds_used} of {report.adds_budget} adds used."
        )
    ]
    for day in report.empty_days[:FIX_LIMIT]:
        out.append(
            f'<p style="margin:8px 0 0;{s.NOTE}color:{s.WARN};">'
            f"Day {day.scoring_period}: {_h(', '.join(day.empty_slots))} going empty</p>"
        )

    rows: list[str] = []
    if report.adds_left == 0:
        out.append(_empty("no adds left this period, so there is nothing to plan today"))
    elif not report.recommended:
        out.append(
            _lede(
                f"Nothing clears the bar ({report.hurdle:.2f} categories, or an empty day "
                f"filled). {report.pool_size} free agents were weighed."
            )
        )
        for move in report.moves[:UNDER_LIMIT]:
            rows.append(_move_row(_side(move, report.today), move.net, _why(move), "under the bar"))
    else:
        for move in report.recommended[:MOVE_LIMIT]:
            rows.append(
                _move_row(_side(move, report.today), move.net, _why(move), "clears the bar")
            )
        # By the man coming in, not by the object: the plan judges its second
        # move with the first already made, so the same add appears in
        # `moves` and in `recommended` with two different numbers, and
        # printing both read as the bar contradicting itself.
        planned = {move.add.player_id for move in report.recommended}
        near = [move for move in report.moves if move.add.player_id not in planned]
        for move in near[:UNDER_LIMIT]:
            rows.append(_move_row(_side(move, report.today), move.net, _why(move), "under the bar"))
    if rows:
        out.append(
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"'
            ' border="0" style="margin-top:12px;border-collapse:collapse;">'
            + "".join(rows)
            + "</table>"
        )
    return "".join(out)


def _swap(move: Swap) -> str:
    coming = ", ".join(player.name for player in move.into)
    if not move.out:
        return f"Add {coming} into the open place"
    return f"Add {coming}, drop {', '.join(player.name for player in move.out)}"


def _season(report: SeasonReport | None, lines: Sequence[str]) -> str:
    if report is None:
        return "".join(_empty(line.strip()) for line in lines) or _empty(
            "no rest-of-season view today"
        )
    record = report.outlook.record_without
    out = [
        _lede(
            f"{report.weeks_remaining:.0f} weeks left. {report.expected_wins:.2f} of 9 "
            f"categories in an ordinary week; on this roster the season ends "
            f"{record[0]:.1f}-{record[1]:.1f}."
        )
    ]
    best = report.recommended
    if best is None:
        nearest = report.moves[0] if report.moves else None
        if nearest is None:
            out.append(
                _empty(f"nothing on the wire moves it; {report.pool_size} free agents weighed")
            )
        else:
            hurdle = nearest.hurdle(report.hurdle_paid, report.hurdle_free)
            out.append(_lede(f"Nothing clears the bar ({hurdle:.2f} a week)."))
            out.append(
                '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"'
                ' border="0" style="margin-top:12px;border-collapse:collapse;">'
                + _move_row(
                    _swap(nearest),
                    nearest.judgement.per_week,
                    _season_why(nearest),
                    "under the bar",
                )
                + "</table>"
            )
    else:
        out.append(
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"'
            ' border="0" style="margin-top:12px;border-collapse:collapse;">'
            + _move_row(_swap(best), best.judgement.per_week, _season_why(best), "clears the bar")
            + "</table>"
        )
    if report.stashes:
        named = ", ".join(
            f"{stash.player.name} (back {stash.expected_return_date:%d %b})"
            for stash in report.stashes[:2]
        )
        out.append(_lede(f"Worth a place when he is back: {named}"))
    return "".join(out)


def _season_why(move: Swap) -> str:
    j = move.judgement
    moved = [shift for shift in move.moved()][:3]
    cats = ", ".join(f"{shift.abbreviation} {shift.delta:+.2f}" for shift in moved)
    return (
        f"{j.per_week:+.2f} categories a week over the rest of it; record "
        f"{j.record_without[0]:.1f}-{j.record_without[1]:.1f} without, "
        f"{j.record_with[0]:.1f}-{j.record_with[1]:.1f} with"
        + (f". Mostly {cats}." if cats else "")
    )


# ---------------------------------------------------------------------------
# What changed
# ---------------------------------------------------------------------------


def _day_of(change: Change) -> date:
    return change.at.date()


def _changed(changes: Sequence[Change]) -> str:
    """The feed's own sentences, grouped by day, newest day first.

    A line about the reader's own team carries the accent rule the page gives
    it, and his opponent's a plain dark one, so the two are told apart by
    where the rule is and not only by its colour (docs/in_season_pages.md).
    """
    if not changes:
        return _empty("nothing you asked about changed")
    out: list[str] = []
    shown = list(changes[:FEED_LIMIT])
    days: list[date] = []
    for change in shown:
        if _day_of(change) not in days:
            days.append(_day_of(change))
    for day in days:
        out.append(
            f'<p style="margin:18px 0 4px;{s.TAG}color:{s.FAINT};">{_h(f"{day:%a %d %b}")}</p>'
        )
        rows = []
        for change in shown:
            if _day_of(change) != day:
                continue
            edge = (
                f"border-left:2px solid {s.ACCENT};padding-left:10px;"
                if change.mine
                else f"border-left:2px solid {s.RULE_STRONG};padding-left:10px;"
                if change.opponent
                else ""
            )
            # The kind rides at the end of the sentence rather than in a
            # column of its own: a second column squeezed the sentence to
            # three words a line at a phone's width, and an email has no
            # media query to widen it back.
            rows.append(
                f'<tr><td style="{s.CELL}{s.TEXT}{edge}">{_h(change.text)} '
                f'<span style="{s.TAG}white-space:nowrap;">{_h(change.kind)}</span></td></tr>'
            )
        out.append(
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"'
            ' border="0" style="border-collapse:collapse;">' + "".join(rows) + "</table>"
        )
    hidden = len(changes) - len(shown)
    if hidden > 0:
        out.append(_lede(f"and {hidden} more"))
    return "".join(out)


# ---------------------------------------------------------------------------
# Standings
# ---------------------------------------------------------------------------


def _standings(digest: Digest) -> str:
    place = digest.place
    if place is None:
        return "".join(_empty(line.strip()) for line in digest.table) or _empty("no standings yet")
    rows = [
        ("Place", f"{place.place} of {place.of}"),
        ("Matchups", f"{place.won}-{place.lost}" + (f"-{place.tied}" if place.tied else "")),
        ("Categories", f"{place.categories_won}-{place.categories_lost}"),
        ("Projected finish", place.projected or "not built yet"),
    ]
    cells = "".join(
        f'<tr><td style="{s.CELL}{s.TAG}width:44%;">{_h(name)}</td>'
        f'<td style="{s.CELL}{s.NUM}font-size:16px;" align="right">'
        f"{_h(value)}</td></tr>"
        for name, value in rows
    )
    tail = (
        _lede("The projected finish is not built yet; this slot is where it will go.")
        if place.projected is None
        else ""
    )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"'
        ' style="margin-top:12px;border-collapse:collapse;">' + cells + "</table>" + tail
    )


# ---------------------------------------------------------------------------
# the footer
# ---------------------------------------------------------------------------


def _link(href: str, words: str) -> str:
    return f'<a href="{_h(href)}" style="color:{s.ACCENT};">{_h(words)}</a>'


def _footer(links: Iterable[tuple[str, str]], why: str, manage: str | None) -> str:
    named = " &nbsp;&#183;&nbsp; ".join(_link(href, words) for href, words in links)
    tail = f" {_link(manage, 'Manage your alerts')}." if manage else ""
    return f"""<tr><td style="padding:30px 20px 26px;">
<div style="border-top:1px solid {s.RULE};padding-top:12px;">
<p style="margin:0;{s.NOTE}font-size:13px;">{named}</p>
<p style="margin:8px 0 0;font-family:{s.MONO};font-size:11px;line-height:1.7;\
color:{s.FAINT};">{_h(why)}{tail}</p>
</div>
</td></tr>
"""


# ---------------------------------------------------------------------------
# the whole page
# ---------------------------------------------------------------------------

WHY_DIGEST = "You get this because you confirmed this address for FCP's morning digest."
WHY_ALERT = "You get this because you asked to be told when a player of yours is ruled out."


def digest_html(
    digest: Digest,
    *,
    wanted: Subscription,
    public_url: str | None = None,
    preview: str = "",
) -> str:
    """The morning digest as a page.

    `wanted` decides the sections and their order: exactly the topics he
    chose, in `app.subscriptions.TOPICS` order. `public_url` is the site, for
    the links at the foot; without it the footer carries the words and no
    links, because a link built on nothing goes nowhere.
    """
    day = f"{digest.generated_at:%A %d %B %Y}"
    out = [
        _open(f"{digest.team_name}: the morning digest", preview or day),
        _masthead(
            digest.league_name or f"Season {digest.season}",
            digest.team_name,
            f"{day} &#183; season {digest.season}".replace("&#183;", "·"),
        ),
    ]
    for title, body in _sections(digest, wanted):
        out.append(_section(title, body))
    out.append(_footer(*_foot(digest, public_url)))
    out.append(_close())
    return "".join(out)


#: The heading over the feed. The five topics that read it are five ways into
#: one feed, not five sections, so the heading is the section's own name and
#: the lede under it says which of them the reader holds.
CHANGED = "What changed"


def _sections(digest: Digest, wanted: Subscription) -> list[tuple[str, str]]:
    """Every section of this reader's email, as (heading, body), in the order
    the topics are listed.

    One topic is not one section either way round. `moves` draws two, because
    "what do I do today" and "where is the season going" are different
    questions with different numbers; the five feed topics draw one between
    them, under the first of them the reader holds.
    """
    out: list[tuple[str, str]] = []
    feed_topics = [topic for topic in wanted.chosen if topic not in (LINEUP, MOVES, STANDINGS)]
    for topic in wanted.chosen:
        if topic == LINEUP:
            out.append((LABELS[topic], _today(digest.today_report, digest.today)))
        elif topic == MOVES:
            out.append(("This week", _week(digest.week_report, digest.opponent_name, digest.plan)))
            out.append(("The season", _season(digest.season_report, digest.season_plan)))
        elif topic == STANDINGS:
            out.append((LABELS[topic], _standings(digest)))
        elif topic == feed_topics[0]:
            held = ", ".join(LABELS[name].lower() for name in feed_topics)
            out.append(
                (CHANGED, _lede(f"You asked for: {held}.") + _changed(wanted.filtered(digest.feed)))
            )
    return out


def _foot(digest: Digest, public_url: str | None) -> tuple[list[tuple[str, str]], str, str | None]:
    base = (public_url or "").rstrip("/")
    if not base:
        return [], WHY_DIGEST + " No link is set on this server.", None
    return (
        [
            (f"{base}/", "This week"),
            (f"{base}/account/alerts", "Alerts"),
        ],
        WHY_DIGEST,
        f"{base}/account/alerts#wants",
    )


def alert_html(
    team: str,
    lines: Sequence[str],
    *,
    when: str,
    public_url: str | None = None,
) -> str:
    """The urgent roster change, as the same page with one section in it."""
    base = (public_url or "").rstrip("/")
    body = "".join(
        f'<p style="margin:12px 0 0;{s.TEXT}color:{s.STOP};">{_h(line)}</p>' for line in lines
    ) or _empty("nothing urgent")
    links = [(f"{base}/", "This week"), (f"{base}/account/alerts", "Alerts")] if base else []
    return "".join(
        [
            _open(f"{team}: a player of yours is out", lines[0] if lines else ""),
            _masthead("Between digests", team, when),
            _section("A player of yours", body),
            _footer(links, WHY_ALERT, f"{base}/account/alerts#wants" if base else None),
            _close(),
        ]
    )


def lines_html(
    league: str,
    lines: Sequence[str],
    *,
    when: str,
    public_url: str | None = None,
) -> str:
    """The plain message as a page, for the one digest that has no `Digest`
    behind it: a league the listener does not follow (docs/jobs.md, "One
    listener league"). Its sections are lines rather than objects, so they
    are drawn as lines -- in the mono face, on the surface, under the same
    masthead -- rather than pretended into a grid they are not.
    """
    base = (public_url or "").rstrip("/")
    body = "".join(
        f'<p style="margin:{"14px" if not line.startswith(" ") else "2px"} 0 0;'
        f"font-family:{s.MONO};font-size:13px;line-height:1.5;"
        f"color:{s.INK if not line.startswith(' ') else s.MUTED};"
        f'{s.TABULAR}white-space:pre-wrap;">{_h(line)}</p>'
        for line in lines
        if line.strip()
    ) or _empty("nothing to report today")
    links = [(f"{base}/", "This week"), (f"{base}/account/alerts", "Alerts")] if base else []
    return "".join(
        [
            _open(f"{league}: the morning digest", when),
            _masthead("Your league", league, when),
            _section("This morning", body),
            _footer(links, WHY_DIGEST, f"{base}/account/alerts#wants" if base else None),
            _close(),
        ]
    )


def plain_html(title: str, eyebrow: str, body_html: str, *, public_url: str | None) -> str:
    """The frame with one section of prose in it: the sign-in link, the
    address-confirmation link. The same masthead and footer as the digest,
    because they are the same product's mail."""
    base = (public_url or "").rstrip("/")
    links = [(f"{base}/", "Full Court Press")] if base else []
    return "".join(
        [
            _open(title, title),
            _masthead(eyebrow, "FCP"),
            _section(title, body_html),
            _footer(links, "You got this because someone asked for it at this address.", None),
            _close(),
        ]
    )


def button(href: str, words: str) -> str:
    """A link drawn as a button: a solid cell in the accent, no radius.

    With the plain URL under it, always, because a client that strips the
    link, a forwarded copy and a reader who does not trust a button all need
    the address itself.
    """
    return f"""<table role="presentation" cellpadding="0" cellspacing="0" border="0"
  style="margin:16px 0 10px;"><tr>
<td bgcolor="{s.ACCENT}" style="background:{s.ACCENT};padding:11px 20px;">
<a href="{_h(href)}" style="font-family:{s.DISPLAY};font-size:14px;letter-spacing:.08em;\
text-transform:uppercase;color:{s.ACCENT_INK};text-decoration:none;">{_h(words)}</a>
</td></tr></table>
<p style="margin:0;font-family:{s.MONO};font-size:11px;line-height:1.6;color:{s.MUTED};\
word-wrap:break-word;word-break:break-all;">{_h(href)}</p>
"""
