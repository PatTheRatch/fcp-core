"""The product's name, written down once.

The product is **Box Out**, at **boxoutfantasy.com**. Until 2026-09-22 it was
called Full Court Press, which is the name of the owner's own league, and
every page, every mail and every script that named it carried that string of
its own. They read this module now, so the next rename is one line and a test
can prove no page still says the old name.

WHAT IS THE BRAND AND WHAT IS NOT

The brand is what a manager sees: a masthead, an eyebrow, a `<title>`, a
mail's subject, a script's `--help`. It is not an identifier. The repository
stays `fcp-core`, the settings stay `FCP_*`, and so do the systemd units, the
tables, the Python packages, the virtualenv and the `fcp_session` cookie:
renaming those is a migration and a deploy and buys a reader nothing.

**A page that names *the league* is not naming the product.** The owner's
league is "Full Court Press" in ESPN's older seasons and in the measurement
notes that cite it ("Full Court Press (ESPN 3853870)"); those are data and
history and stay exactly as they are.

HOW A PAGE READS IT

The site's pages are static files read per request (docs/site.md), so they
carry tokens -- `{{brand}}`, `{{brand_domain}}`, `{{brand_tagline}}` -- and
every route that serves one passes it through `fill` first: `app/api/site.py`,
`app/api/pages.py` (the shell script is an asset), `app/api/auth.py`,
`app/api/leagues_admin.py`, the draft room's screen and the draft plan's
template. `fill` is a plain `str.replace`, not a template engine: the files
are HTML, CSS and JavaScript full of braces, and nothing here should be able
to evaluate any of it.

Python that builds its own markup or its own subject line imports the
constants instead.
"""

#: What a manager sees, everywhere the product names itself.
BRAND = "Box Out"

#: Where it lives. The public address is `https://{BRAND_DOMAIN}`, which the
#: API knows as `FCP_PUBLIC_URL`; this is the bare host, for prose and for a
#: footer that prints an address rather than linking one.
BRAND_DOMAIN = "boxoutfantasy.com"

#: One line, plain, under the name: what it does, in the currency the league
#: is actually played in.
BRAND_TAGLINE = (
    "Category-league fantasy basketball, judged in the currency your league counts: category wins"
)

#: The tokens a static file may carry, and what each becomes. Distinctive
#: enough that no stylesheet or script contains one by accident.
TOKENS: dict[str, str] = {
    "{{brand}}": BRAND,
    "{{brand_domain}}": BRAND_DOMAIN,
    "{{brand_tagline}}": BRAND_TAGLINE,
}


def fill(text: str) -> str:
    """Every brand token in a served file, replaced by what it stands for.

    A file with no token comes back unchanged, which is most of them: the
    stylesheet and `pages.js` go through this on their way out and neither
    names the product.
    """
    for token, value in TOKENS.items():
        text = text.replace(token, value)
    return text
