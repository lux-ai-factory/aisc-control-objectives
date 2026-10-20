"""The assessment page laid out like the qualification's card: a menu on the left shows one part at a
time (the risk register, the matrix), the site's links sit at the bottom of that menu, "← Back" stays
in the header as a button of its own, and the title is at the top of the panel beside the menu."""
from __future__ import annotations

import re


def _page(http, platform_project, assessment) -> str:
    response = http.get(f"/p/{platform_project}/projects/{assessment}")
    assert response.status_code == 200, response.text
    return response.text


def _menu(page: str) -> str:
    match = re.search(r'<aside class="co-sidemenu">(.*?)</aside>', page, re.S)
    assert match, "the page has no left menu"
    return match.group(1)


def test_the_menu_lists_the_register_then_the_matrix(client, start, platform_project):
    http, _ = client
    page = _page(http, platform_project, start())
    parts = re.findall(r'<a href="#(\w+)" data-part="\1"', _menu(page))
    assert parts == ["register", "matrix"]
    menu = _menu(page)
    assert menu.index("Rate the risks") < menu.index("Link objectives to risks")


def test_each_part_is_marked_for_the_menu_to_show_one_at_a_time(client, start, platform_project):
    http, _ = client
    page = _page(http, platform_project, start())
    assert re.search(r'<details class="qf-section co-fold co-part" open id="register">', page)
    assert re.search(r'<details class="qf-section co-fold co-part" open id="matrix">', page)
    assert "/static/sidemenu.js" in page


def test_the_site_links_sit_at_the_bottom_of_the_menu(client, start, platform_project):
    http, _ = client
    page = _page(http, platform_project, start())
    menu = _menu(page)
    site = menu[menu.index('class="co-sidemenu-site"'):]
    for link in ("/objectives", "/sets", "/profiles", "/projects"):
        assert f'href="/p/{platform_project}{link}"' in site, link
    assert menu.index("Link objectives to risks") < menu.index('class="co-sidemenu-site"')
    # the header's own copy gives way while the menu is on the page
    assert "body:has(.co-sidemenu) .site-header .site-nav { display: none; }" in page


def test_back_is_a_button_of_its_own_in_the_header(client, start, platform_project):
    http, _ = client
    page = _page(http, platform_project, start())
    header = re.search(r'<header class="site-header">(.*?)</header>', page, re.S).group(1)
    back = re.search(r'<a class="btn btn--ghost btn--small site-back" href="[^"]+"[^>]*>&larr; Back to the project</a>', header)
    assert back, header
    nav = re.search(r'<nav class="site-nav">(.*?)</nav>', header, re.S).group(1)
    assert "Back" not in nav
    assert "Back" not in _menu(page)


def test_back_is_the_same_button_on_the_other_pages(client, platform_project):
    http, _ = client
    page = http.get(f"/p/{platform_project}/sets").text
    assert re.search(r'<a class="btn btn--ghost btn--small site-back"', page)
    assert "co-sidemenu" not in page.split("<style>")[1].split("</style>")[1]


def test_the_title_is_at_the_top_of_the_panel(client, start, platform_project):
    http, _ = client
    page = _page(http, platform_project, start())
    panel = page.index('<div class="co-sidepanel">')
    assert panel < page.index('<div class="co-header">') < page.index('id="register"')
    assert panel < page.index('<div class="co-profile">')
    assert page.index('<aside class="co-sidemenu">') < panel


def test_suggesting_with_ai_comes_back_to_the_matrix(client, start, platform_project):
    http, _ = client
    assessment = start()
    response = http.post(f"/p/{platform_project}/projects/{assessment}/map")
    assert response.status_code == 303
    assert response.headers["location"].endswith("#matrix")


def test_saving_the_ratings_comes_back_to_the_register(client, start, platform_project):
    http, _ = client
    assessment = start()
    response = http.post(f"/p/{platform_project}/projects/{assessment}/severity", data={"impact:risk2": "4"})
    assert response.status_code == 303
    assert response.headers["location"].endswith("#register")


def test_the_menu_is_the_only_way_back_to_the_projects(client, start, platform_project):
    """The left menu already links to Projects, and the header has "← Back": a third link in the
    panel was one too many. An assessment is not the Projects page, so the menu does not mark it."""
    http, _ = client
    page = _page(http, platform_project, start())
    assert "← Projects" not in page
    assert not re.search(r'href="[^"]+/projects" class="nav-here"', page)


def test_saving_a_risks_objectives_comes_back_to_its_row_of_the_matrix(client, start, platform_project):
    """The address names the row; sidemenu.js shows the part holding it, the matrix."""
    http, _ = client
    assessment = start()
    response = http.post(f"/p/{platform_project}/projects/{assessment}/risks/risk0/mapping", data={"objective": "O5"})
    assert response.status_code == 303
    assert response.headers["location"].endswith("#map-risk0")
    page = _page(http, platform_project, assessment)
    matrix = page[page.index('id="matrix"'):]
    assert re.search(r'<tr class="co-mrow" data-risk="risk0" id="map-risk0">', matrix)
    assert page.count('id="map-risk0"') == 1


def test_the_page_asks_before_leaving_with_unsaved_changes(client, start, platform_project):
    """Ratings, rationales, key ticks and a risk's objectives are separate forms on one page, and
    the other buttons reload it: static/unsaved.js asks before what was typed is lost."""
    http, _ = client
    page = _page(http, platform_project, start())
    assert '<script src="/static/unsaved.js" defer></script>' in page
    script = http.get("/static/unsaved.js")
    assert script.status_code == 200
    assert "Leave without saving? What you changed here will be lost." in script.text


def test_closing_a_risks_edit_popover_without_saving_drops_its_ticks(client, start, platform_project):
    """The ticks left in a closed popover came back on the next opening as if saved; the script
    resets that form when the popover closes."""
    http, _ = client
    page = _page(http, platform_project, start())
    assert re.search(r'<div id="edit-risk0" class="co-edit-pop" popover>\s*<form ', page)
    script = http.get("/static/unsaved.js").text
    assert '".co-edit-pop"' in script and "form.reset()" in script


def test_the_page_is_given_only_what_it_shows(client, start, platform_project, monkeypatch):
    """Objective labels, flag tags and whether a risk is rated were worked out for the page and never shown."""
    from aisc_control_objectives import rendering

    given = {}
    environment = rendering._environment()
    page = environment.get_template("project.html.j2")
    get_template = environment.get_template

    class Spy:
        def render(self, **context):
            given.update(context)
            return page.render(**context)

    monkeypatch.setattr(environment, "get_template",
                        lambda name, *args, **kwargs: Spy() if name == "project.html.j2"
                        else get_template(name, *args, **kwargs))
    http, _ = client
    _page(http, platform_project, start())
    assert given and "objective_labels" not in given and "flag_tags" not in given
    assert not hasattr(rendering._RiskView, "rated")
