"""What the pages call things. The list of a project's assessments is Assessments (its address is still
/projects), the built-in short profile is Fairness and oversight (its id is still fairness-oversight),
and REQ1 ... REQ11 are dimensions."""
from __future__ import annotations

import re

from step2_support import _api


def test_the_assessments_page_is_called_assessments(client, start, platform_project):
    http, _ = client
    start()
    page = http.get(f"/p/{platform_project}/projects").text
    assert "<title>Assessments · AI Act Control Objectives</title>" in page
    assert "<h1>Assessments</h1>" in page
    assert re.search(rf'<a href="/p/{platform_project}/projects"[^>]*>Assessments</a>', page)
    assert ">Projects<" not in page


def test_an_older_assessment_points_to_the_assessments_page(client, start, platform_project):
    http, _ = client
    first = start(1)
    start(2)
    page = " ".join(http.get(f"/p/{platform_project}/projects/{first}").text.split())
    assert "from the Assessments page" in page and "projects page" not in page


def test_an_assessment_with_no_risks_links_back_to_assessments(client, platform_project, system_version):
    http, projects = client
    empty = [{"@id": "urn:x#system", "@type": ["https://w3id.org/airo#AISystem"]}]
    import json

    made = projects.create(platform_project, "Empty", json.dumps(empty), empty, system_version(platform_project, 1))
    page = http.get(f"/p/{platform_project}/projects/{made.record.id}").text
    assert "← Assessments" in page and "← Projects" not in page


def test_the_objectives_page_says_dimensions(client):
    http, _ = client
    page = http.get("/objectives").text
    shown = re.sub(r"<(style|script)\b.*?</\1>", "", page, flags=re.S)
    assert "macro requirement" not in shown.lower()
    assert "11 dimensions" in " ".join(page.split()) and ">Dimensions<" in page


def test_the_payload_keeps_the_profiles_id(client, start, platform_project):
    from aisc_control_objectives.library import FAIRNESS_OVERSIGHT

    http, _ = client
    a = start()
    http.post(_api(platform_project, a, "/profile"), json={"profile_id": FAIRNESS_OVERSIGHT})
    profile = http.get(_api(platform_project, a)).json()["profile"]
    assert (profile["id"], profile["label"]) == ("fairness-oversight", "Fairness and oversight")


# The words the assessor reads on step 2

def _shown(page: str) -> str:
    """The page's text as a reader meets it: no style, no script, no tags, one space between words."""
    page = re.sub(r"<(style|script)\b.*?</\1>", "", page, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", page).split())


def test_the_site_links_put_assessments_first(client, start, platform_project):
    http, _ = client
    page = http.get(f"/p/{platform_project}/projects").text
    nav = re.search(r'<nav class="site-nav">(.*?)</nav>', page, re.S).group(1)
    names = [n.strip() for n in re.findall(r"<a [^>]*>([^<]+)</a>", nav)]
    assert names == ["Assessments", "Objective catalogue", "Custom objective sets", "Objective profiles"]


def test_the_assessment_speaks_of_priority_risk_level_and_links(client, start, platform_project):
    http, _ = client
    a = start()
    http.post(_api(platform_project, a, "/ratings"), json={"risk2": {"impact": 5, "likelihood": 4}})
    http.post(_api(platform_project, a, "/map"))
    page = http.get(f"/p/{platform_project}/projects/{a}").text
    shown = _shown(page)
    menu = re.search(r'<aside class="co-sidemenu">(.*?)</aside>', page, re.S).group(1)
    assert re.search(r"1</span> Rate the risks", menu) and re.search(r"2</span> Link objectives to risks", menu)
    assert re.search(r'<span class="n">\d+ priority objectives</span>', menu)
    assert shown.count("Risk level (impact × likelihood)") == 2
    assert "<dt>Priority score</dt>" in page and "<dt>Linked by</dt><dd>AI suggestion" in page
    assert "Priority objective" in shown and "Save priority choices" in shown
    assert "Also replace objectives I linked by hand" in shown
    assert "Manage › AI assistants › AI assistant models and keys" in shown
    assert '<option value="" selected>Not rated</option>' in page
    for gone in ("Key objective", "key by default", "Risk register", "Risk and control matrix", "Full information",
                 "Typed in VAIR", "Borne by", "Manage, Keys", "AIRO", "mappings", "Mapped by", " map "):
        assert gone not in shown, gone
    assert "–" not in shown and "—" not in shown


def test_the_hashes_fold_away_under_technical_details(client, start, platform_project, repository):
    http, _ = client
    a = start()
    page = http.get(f"/p/{platform_project}/projects/{a}").text
    tech = re.search(r'<details class="co-tech">\s*<summary>Technical details</summary>(.*?)</details>', page, re.S)
    assert tech and repository.get(a).digest[:12] in tech.group(1)
    assert repository.get(a).digest[:12] not in page.replace(tech.group(0), "")


def test_what_the_ai_could_not_do_is_said_in_plain_sentences(client, start, platform_project, mapper):
    http, _ = client
    a = start()
    mapper.by_risk["risk0"] = []
    http.post(_api(platform_project, a, "/map"))
    page = http.get(f"/p/{platform_project}/projects/{a}").text
    tech = re.search(r'<details class="co-tech">.*?</details>', page, re.S).group(0)
    shown = _shown(page.replace(tech, ""))
    assert "The AI suggested no objective for this risk" in shown
    for code in ("risk-unmapped", "fixpoint", "stopped on"):
        assert code not in shown, code


def test_each_page_is_headed_as_its_site_link_names_it(client, platform_project):
    http, _ = client
    for path, name in (("objectives", "Objective catalogue"), ("sets", "Custom objective sets"),
                       ("profiles", "Objective profiles")):
        page = http.get(f"/p/{platform_project}/{path}").text
        assert f"<h1>{name}</h1>" in page, path


def test_the_assessments_list_speaks_of_links_and_priority(client, start, platform_project):
    http, _ = client
    a = start()
    http.post(_api(platform_project, a, "/ratings"), json={"risk2": {"impact": 5, "likelihood": 4}})
    assert "Ready to link" in http.get(f"/p/{platform_project}/projects").text
    http.post(_api(platform_project, a, "/map"))
    shown = _shown(http.get(f"/p/{platform_project}/projects").text)
    assert re.search(r"\d+ priority objectives? ", shown)
    for gone in ("AIRO", "mappings", "key choices"):
        assert gone not in shown, gone
