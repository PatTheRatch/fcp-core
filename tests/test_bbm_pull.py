import pytest

from app.draft.bbm_pull import BBMPullError, custom_selects, form_state, logged_out, page_season


def dropdown(name: str, options: list[tuple[str, str]], selected: str, wide: str = "") -> str:
    """The markup BBM renders for one of its scripted dropdowns."""
    label = dict(options)[selected]
    items = "".join(
        f'<div data-value="{value}" class="bm-custom-select-option'
        f'{" selected" if value == selected else ""}">{text}</div>'
        for value, text in options
    )
    return (
        f'<div data-name="{name}" class="bm-custom-select{wide}">'
        '<div class="bm-custom-select-trigger">'
        f'<span class="bm-custom-select-value">{label}</span>'
        '<span class="bm-custom-select-arrow"></span></div>'
        f'<div class="bm-custom-select-options">{items}</div></div>'
    )


PAGE = """
<h1>Projections 26-27</h1>
<form method="post" action="./projections.aspx" id="form1">
<input type="hidden" name="__VIEWSTATE" value="abc&amp;def" />
<input type="hidden" name="__EVENTTARGET" value="" />
<input name="pos_4" type="checkbox" value="4" checked="checked" />
<input name="pos_9" type="checkbox" value="9" />
<input name="cvtype_X" type="radio" value="per" checked />
<input name="cvtype_X" type="radio" value="total" />
<input name="pageTitleRowSearch-search" type="text" />
<input name="Go" type="submit" value="Go" />
{value_type}
{league}
</form>
""".format(
    value_type=dropdown(
        "ValueDisplayType", [("Total", "Total Games Value"), ("PerGame", "Per Game Value")], "Total"
    ),
    league=dropdown(
        "pageTitleRowLeague", [("295923", "Brighton &amp; Co")], "295923", " bm-custom-select--wide"
    ),
)


def test_form_state_posts_what_a_browser_would() -> None:
    form = form_state(PAGE)
    assert form["__VIEWSTATE"] == "abc&def"
    assert form["pos_4"] == "4"
    assert "pos_9" not in form
    assert form["cvtype_X"] == "per"
    assert form["pageTitleRowSearch-search"] == ""
    assert "Go" not in form
    assert form["ValueDisplayType"] == "Total"
    assert form["pageTitleRowLeague"] == "295923"


def test_custom_selects_carry_the_selected_label() -> None:
    assert ("pageTitleRowLeague", "295923", "Brighton & Co") in custom_selects(PAGE)


def test_page_season_reads_the_title() -> None:
    assert page_season(PAGE) == 2027
    with pytest.raises(BBMPullError):
        page_season("<h1>Rankings</h1>")


def test_logged_out() -> None:
    assert logged_out('<input name="PasswordTB" type="password" />')
    assert logged_out("You must be logged in to view this")
    assert not logged_out(PAGE)
