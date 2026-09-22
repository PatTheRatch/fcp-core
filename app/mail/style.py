"""The house style, written out for an inbox.

An email client is not a browser. It strips `<style>` blocks (Gmail keeps
them, Outlook's Word engine throws most of them away), it does not fetch a
stylesheet, it does not run script, it does not know a custom property, and
Outlook lays a page out with Word rather than a browser engine. So the site's
`pages.css` cannot be reused: what is here is the same design said again in
the only vocabulary an email has -- inline attributes on table cells, literal
colours, and system fonts.

WHAT IS COPIED, AND FROM WHERE

The colours are `pages.css`'s light palette, value for value, because the
email is a page of the site and a reader should not be able to tell which of
the two he is looking at. They are written as literal hex because
`var(--ink)` means nothing in an inbox. When the palette moves, it moves
here too, by hand -- the one place in this product where a colour is written
twice, and the cost of an email that looks like the site.

The faces are the site's fallbacks. The site loads Oswald, Source Serif 4
and IBM Plex Mono from Google; an email may not load a font at all, and a
page that waits for one shows nothing. So the display face is the site's
fallback (`Arial Narrow`), the body face is Georgia, which is the fallback
`pages.css` names beside Source Serif 4, and the mono face is the usual
stack. A client with none of them falls back through the generic family at
the end, which is why every stack ends in one.

THE RULES OF THE HOUSE still hold: rules, not boxes; no rounded corners, no
chips, no shadows; one burnt-orange accent; tabular numerals wherever there
is a number. A border-bottom on a table cell is a rule, and that is the whole
of the visual language here.
"""

from __future__ import annotations

# --- the palette: pages.css's light half, value for value -----------------
GROUND = "#E8E6DF"
SURFACE = "#F7F6F2"
SUNK = "#DEDBD2"
RULE = "#C6C2B6"
RULE_STRONG = "#16181A"
INK = "#16181A"
MUTED = "#5E6167"
FAINT = "#8E9096"
ACCENT = "#C4551F"
ACCENT_INK = "#FFFFFF"
ACCENT_SOFT = "#F1DCCF"
GOOD = "#2F6F4E"
WARN = "#9A6A00"
STOP = "#B23A30"

# --- the faces: the site's fallbacks, because no font is loaded -----------
#: The site's `--display` without its web font: Oswald's own fallback.
DISPLAY = "'Arial Narrow', Arial, Helvetica, sans-serif"
#: The site's `--body` without Source Serif 4: the fallback it names.
BODY = "Georgia, 'Times New Roman', Times, serif"
#: The site's `--mono` without IBM Plex Mono.
MONO = "Menlo, Consolas, 'Courier New', Courier, monospace"

#: The column. 600px is the width every client shows without scaling, and
#: the width the layout below is drawn at; on a phone the table shrinks to
#: the viewport because its width is capped rather than set.
WIDTH = 600

#: Numbers line up in columns, where the client allows it. Harmless where it
#: does not: an unknown declaration is dropped, not an error.
TABULAR = "font-variant-numeric:tabular-nums;"

#: An eyebrow: the small mono capitals over a heading.
EYEBROW = (
    f"font-family:{MONO};font-size:10px;letter-spacing:.16em;"
    f"text-transform:uppercase;color:{MUTED};"
)
#: A section heading: the display face, uppercase, a hard rule under it.
HEAD = (
    f"font-family:{DISPLAY};font-size:19px;font-weight:bold;text-transform:uppercase;"
    f"letter-spacing:.02em;color:{INK};"
)
#: Ordinary prose.
TEXT = f"font-family:{BODY};font-size:15px;line-height:1.5;color:{INK};"
#: Prose that is a note rather than the thing itself.
NOTE = f"font-family:{BODY};font-size:14px;line-height:1.5;color:{MUTED};"
#: A number.
NUM = f"font-family:{MONO};font-size:14px;{TABULAR}color:{INK};"
#: The little uppercase label at the end of a row.
TAG = (
    f"font-family:{DISPLAY};font-size:10px;letter-spacing:.07em;"
    f"text-transform:uppercase;color:{MUTED};"
)

#: A cell in a listing: the rule under it is the whole of the line work.
CELL = f"padding:8px 10px;border-bottom:1px solid {RULE};"
#: The first cell of a listing, under its header: a hard rule instead.
CELL_TOP = f"padding:8px 10px;border-bottom:1px solid {RULE_STRONG};"


def gain(value: float) -> str:
    """A number that went up or down, told apart by a sign and an arrow
    before it is told apart by colour.

    A colour alone is no answer to a reader who cannot see it, and an inbox
    is worse than a browser for this: a client in dark mode may invert every
    colour on the page. So the arrow carries the meaning and the colour
    agrees with it (docs/trades.md, "The page").
    """
    if value > 0:
        return f'<span style="color:{GOOD}">&#8593; {value:+.2f}</span>'
    if value < 0:
        return f'<span style="color:{STOP}">&#8595; {value:+.2f}</span>'
    return f'<span style="color:{FAINT}">&#8212; {value:+.2f}</span>'
