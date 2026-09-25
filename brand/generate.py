#!/usr/bin/env python3
"""Generate the Box Out Fantasy logo system as SVG.

Every glyph is a hand-drawn path, so the output has no font dependencies.
Run from the brand/ directory:  python3 generate.py
"""
from pathlib import Path

INK = "#2B2D31"
ORANGE = "#F26A1B"
PAPER = "#F7F6F3"
DARK = "#0D0F12"   # matches --canvas (dark) in docs/design_system.md

OUT = Path(__file__).parent


# ---------------------------------------------------------------- symbol
# Native symbol geometry: spans x 120..1080, y 260..800 (960 x 540),
# centre (600, 530).
def symbol(ink, orange, cx, cy, s, square=False):
    sq = f'<rect x="240" y="595" width="100" height="100" fill="{orange}"/>' if square else ""
    return f'''<g transform="translate({cx},{cy}) scale({s}) translate(-600,-530)">
  <g fill="none" stroke="{ink}" stroke-width="80" stroke-linejoin="round" stroke-linecap="round">
    <path d="M160 300 V760"/>
    <path d="M160 300 H350 Q420 300 420 370 V460 Q420 530 350 530 H160"/>
    <path d="M160 530 H380 Q460 530 460 610 V680 Q460 760 380 760 H160"/>
    <circle cx="810" cy="530" r="230"/>
  </g>
  {sq}
  <circle cx="810" cy="530" r="165" fill="{orange}"/>
  <g clip-path="url(#ball)" fill="none" stroke="{ink}" stroke-width="16">
    <path d="M810 360 V700"/><path d="M640 530 H980"/>
    <path d="M690 400 Q760 530 690 660"/><path d="M930 400 Q860 530 930 660"/>
  </g>
</g>'''


# -------------------------------------------------------------- wordmark
# Letter box 100 x 140, stroke 28, square caps. Native width 790, height 140.
_L = {
    "B": '<path d="M14 14 V126"/><path d="M14 14 H66 Q84 14 84 32 V52 Q84 70 66 70 H14"/>'
         '<path d="M14 70 H70 Q86 70 86 86 V110 Q86 126 70 126 H14"/>',
    "O": '<rect x="14" y="14" width="72" height="112" rx="26"/>',
    "X": '<g clip-path="url(#lb)" stroke-linecap="butt"><path d="M0 0 L100 140"/><path d="M100 0 L0 140"/></g>',
    "U": '<path d="M14 14 V98 Q14 126 42 126 H58 Q86 126 86 98 V14"/>',
    "T": '<path d="M14 14 H86"/><path d="M50 14 V126"/>',
}
_WORD_X = [("B", 0), ("O", 130), ("X", 260), ("O", 430), ("U", 560), ("T", 690)]
WORD_W = 790


def wordmark(ink, cx, cy, s):
    inner = "".join(f'<g transform="translate({x},0)">{_L[c]}</g>' for c, x in _WORD_X)
    return (f'<g transform="translate({cx - WORD_W / 2 * s},{cy - 70 * s}) scale({s})" fill="none" '
            f'stroke="{ink}" stroke-width="28" stroke-linecap="square" stroke-linejoin="miter">{inner}</g>')


# --------------------------------------------------------------- tagline
# Letter box 30 x 40, stroke 9, advance 56. Native block incl. rules: 800 x 40.
_T = {
    "F": '<path d="M5 35 V5 H25"/><path d="M5 20 H21"/>',
    "A": '<path d="M5 35 L15 5 L25 35"/><path d="M9.5 24 H20.5"/>',
    "N": '<path d="M5 35 V5 L25 35 V5"/>',
    "T": '<path d="M5 5 H25"/><path d="M15 5 V35"/>',
    "S": '<g stroke-linecap="butt"><path d="M30 5 H10 Q5 5 5 10 V15 Q5 20 10 20 H20 Q25 20 25 25 V30 Q25 35 20 35 H0"/></g>',
    "Y": '<path d="M5 5 L15 20 L25 5"/><path d="M15 20 V35"/>',
}


def tagline(ink, orange, cx, cy, s):
    letters = "".join(f'<g transform="translate({217 + i * 56},0)">{_T[c]}</g>' for i, c in enumerate("FANTASY"))
    return f'''<g transform="translate({cx - 400 * s},{cy - 20 * s}) scale({s})">
  <g fill="none" stroke="{ink}" stroke-width="9" stroke-linecap="square" stroke-linejoin="round">{letters}</g>
  <g stroke="{orange}" stroke-width="8"><path d="M0 20 H175"/><path d="M625 20 H800"/></g>
</g>'''


# ---------------------------------------------------------------- layouts
DEFS = '''<defs>
    <clipPath id="ball"><circle cx="810" cy="530" r="165"/></clipPath>
    <clipPath id="lb"><rect x="0" y="0" width="100" height="140"/></clipPath>
  </defs>'''


def svg(w, h, title, bg, body, rx=0):
    bgrect = f'<rect width="{w}" height="{h}" rx="{rx}" fill="{bg}"/>' if bg else ""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">
  <title>{title}</title>
  {DEFS}
  {bgrect}
  {body}
</svg>
'''


def stacked(bg, ink, orange, with_tagline, square=False):
    """Symbol above BOX OUT (optionally FANTASY). 1200 x 1200."""
    ms = 0.88                      # symbol scale -> 845 x 475
    ws = 845 / WORD_W              # wordmark width matches symbol width
    gap = 48
    tag_gap, tag_h = 44, 40
    total = 540 * ms + gap + 140 * ws + ((tag_gap + tag_h) if with_tagline else 0)
    top = (1200 - total) / 2
    sym_cy = top + 540 * ms / 2
    word_cy = top + 540 * ms + gap + 70 * ws
    body = symbol(ink, orange, 600, sym_cy, ms, square) + wordmark(ink, 600, word_cy, ws)
    if with_tagline:
        tag_cy = top + 540 * ms + gap + 140 * ws + tag_gap + tag_h / 2
        body += tagline(ink, orange, 600, tag_cy, 1.0)
    return svg(1200, 1200, "Box Out Fantasy", bg, body)


def horizontal(bg, ink, orange, square=False):
    """Symbol beside BOX OUT. 1600 x 520."""
    ws = 1.0
    ms = (1.7 * 140 * ws) / 540    # symbol height = 1.7 x cap height
    gap = 64
    total = 960 * ms + gap + WORD_W * ws
    left = (1600 - total) / 2
    body = symbol(ink, orange, left + 960 * ms / 2, 260, ms, square)
    body += wordmark(ink, left + 960 * ms + gap + WORD_W * ws / 2, 260, ws)
    return svg(1600, 520, "Box Out Fantasy", bg, body)


def icon(bg, ink, orange, square=False):
    """Symbol only on a rounded square. 1200 x 1200."""
    return svg(1200, 1200, "Box Out Fantasy mark", bg, symbol(ink, orange, 600, 600, 1.0, square), rx=240)


def mark(ink, orange, square=False):
    """Symbol only, transparent background, tight viewBox. 1040 x 620."""
    return svg(1040, 620, "Box Out Fantasy mark", None, symbol(ink, orange, 520, 310, 1.0, square))


def compare():
    """Side-by-side test: B with orange square vs. without. 2400 x 1200."""
    a = stacked(PAPER, INK, ORANGE, False, square=True)
    b = stacked(PAPER, INK, ORANGE, False, square=False)
    strip = lambda s: s.split("</defs>", 1)[1].rsplit("</svg>", 1)[0]
    body = f'<g>{strip(a)}</g><g transform="translate(1200,0)">{strip(b)}</g>'
    body += f'<path d="M1200 0 V1200" stroke="{INK}" stroke-width="2" stroke-opacity="0.2"/>'
    body += (f'<g font-family="sans-serif" font-size="36" fill="{INK}" fill-opacity="0.55">'
             f'<text x="600" y="80" text-anchor="middle">A: with orange square</text>'
             f'<text x="1800" y="80" text-anchor="middle">B: without</text></g>')
    return svg(2400, 1200, "Compare", None, body)


FILES = {
    "box-out-primary.svg":            stacked(PAPER, INK, ORANGE, False),
    "box-out-primary-dark.svg":       stacked(DARK, PAPER, ORANGE, False),
    "box-out-primary-horizontal.svg": horizontal(PAPER, INK, ORANGE),
    "box-out-primary-horizontal-dark.svg": horizontal(DARK, PAPER, ORANGE),
    "box-out-secondary.svg":          stacked(PAPER, INK, ORANGE, True),
    "box-out-secondary-dark.svg":     stacked(DARK, PAPER, ORANGE, True),
    "box-out-icon.svg":               icon(DARK, PAPER, ORANGE),
    "box-out-mark.svg":               mark(INK, ORANGE),
    "box-out-mark-light.svg":         mark(PAPER, ORANGE),
}

if __name__ == "__main__":
    for name, content in FILES.items():
        (OUT / name).write_text(content)
    (OUT / "compare-square.svg").write_text(compare())
    print(f"wrote {len(FILES) + 1} files to {OUT}")
