"""The mapping archive keeps, row for row, what a card update replaced: it is read back as written."""
import json

from sqlalchemy import text

from test_migration_project_database import migrated  # noqa: F401 (fixture)


def test_the_archive_keeps_what_a_card_update_replaced(migrated):  # noqa: F811
    replaced = [{"risk": "risk0", "objective": "O1", "quote": "q", "rationale": "r", "source": "ai"}]
    with migrated.begin() as connection:
        connection.execute(text("INSERT INTO control_objectives.mapping_archive (project_id, reason, rows)"
                                " VALUES ('a1', 'card', CAST(:rows AS jsonb))"), {"rows": json.dumps(replaced)})
        kept = connection.execute(text("SELECT reason, rows FROM control_objectives.mapping_archive"
                                       " WHERE project_id = 'a1'")).one()
    assert kept.reason == "card" and kept.rows == replaced
