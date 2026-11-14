"""Two assessors open the same assessment. One rates a risk and saves; the other, on the page loaded before,
rates another risk and saves. The second save keeps the first one's ratings and rationale: it saves what that
person changed, or it is refused with a word to reload. It never writes back what the stale page showed."""
from __future__ import annotations

from html.parser import HTMLParser

from step2_support import _api


class RatingsForm(HTMLParser):
    """The fields the ratings form (form#ratings) would post, as the browser would post them unchanged."""

    def __init__(self):
        super().__init__()
        self.fields: dict[str, str] = {}
        self._in_form = False
        self._select = None
        self._textarea = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self._in_form = a.get("id") == "ratings"
        if not self._in_form:
            return
        if tag == "select":
            self._select = a.get("name")
            self.fields.setdefault(self._select, "")
        elif tag == "option" and self._select and "selected" in a:
            self.fields[self._select] = a.get("value", "")
        elif tag == "textarea":
            self._textarea = a.get("name")
            self.fields[self._textarea] = ""
        elif tag == "input" and a.get("name") and a.get("type", "text") not in ("submit", "button"):
            self.fields[a["name"]] = a.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self._in_form = False
        elif tag == "select":
            self._select = None
        elif tag == "textarea":
            self._textarea = None

    def handle_data(self, data):
        if self._textarea:
            self.fields[self._textarea] += data


def ratings_form_of(http, pid, assessment) -> dict[str, str]:
    page = http.get(f"/p/{pid}/projects/{assessment}")
    assert page.status_code == 200, page.text
    parser = RatingsForm()
    parser.feed(page.text)
    assert "impact:risk0" in parser.fields and "impact:risk4" in parser.fields, parser.fields
    return parser.fields


def severity_of(http, pid, assessment) -> dict:
    return http.get(_api(pid, assessment)).json()["severity"]


def test_a_second_assessors_save_keeps_the_first_ones_ratings(client, start, platform_project):
    http, _ = client
    a = start()
    severity = f"/p/{platform_project}/projects/{a}/severity"
    first_page = ratings_form_of(http, platform_project, a)
    second_page = ratings_form_of(http, platform_project, a)       # opened before the first one saves

    first = {**first_page, "impact:risk0": "5", "likelihood:risk0": "5", "comment:risk0": "Wrong referral harms a minor"}
    assert http.post(severity, data=first).status_code == 303

    second = {**second_page, "impact:risk4": "3", "likelihood:risk4": "3"}
    saved = http.post(severity, data=second)

    now = severity_of(http, platform_project, a)
    assert now["impact"].get("risk0") == 5 and now["likelihood"].get("risk0") == 5, (
        f"the second save wrote back what its stale page showed: {now}")
    assert "Wrong referral harms a minor" in str(now), f"the first assessor's rationale is gone: {now}"
    if saved.status_code == 303:
        assert now["impact"].get("risk4") == 3 and now["likelihood"].get("risk4") == 3, now
