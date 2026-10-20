"""Which project a request is in.

Who may read or change it is aisc_identity.access's rule, held to the shared
truth table in shared/identity/access-cases.json.
"""

from aisc_control_objectives.access import project_from_path


class TestWhichProjectARequestIsIn:
    def test_reads_the_slug_out_of_the_path(self):
        assert project_from_path("/p/mcas") == "mcas"
        assert project_from_path("/p/mcas/objectives") == "mcas"

    def test_does_not_decode_a_second_time(self):
        """The path is the router's, which the server has decoded once already.
        A second decode would make the gate check `a b` while the handler is
        given `a%20b`; the gate checks the very string the handler receives."""
        assert project_from_path("/p/a b/x") == "a b"
        assert project_from_path("/p/a%20b/x") == "a%20b"

    def test_is_none_outside_a_project(self):
        for path in ("/", "/objectives", "/health", "/static/app.css", "/p/", "/p"):
            assert project_from_path(path) is None, path
