"""objective ids O1 ... O50

The objectives are numbered O1 ... O50 in catalogue order instead of R1.1 ... R11.4.
Every stored id is renamed by the table below (a copy of data/objective_id_renames.csv, kept here so
a later edit of that file never changes what this revision did). A selection is rewritten in
catalogue order; an id the table does not know (a stale one) is left as it is.

Revision ID: 20261001100000_objective_ids
Revises: 20261001000000_selection
"""
from alembic import op

revision = "20261001100000_objective_ids"
down_revision = "20261001000000_selection"
branch_labels = None
depends_on = None

RENAMES = (
    ('R1.1', 'O1'),
    ('R1.2', 'O2'),
    ('R1.3', 'O3'),
    ('R1.4', 'O4'),
    ('R2.1', 'O5'),
    ('R2.2', 'O6'),
    ('R2.3', 'O7'),
    ('R2.4', 'O8'),
    ('R2.5', 'O9'),
    ('R2.6', 'O10'),
    ('R3.1', 'O11'),
    ('R3.2', 'O12'),
    ('R3.3', 'O13'),
    ('R3.4', 'O14'),
    ('R3.5', 'O15'),
    ('R3.6', 'O16'),
    ('R4.1', 'O17'),
    ('R4.2', 'O18'),
    ('R4.3', 'O19'),
    ('R4.4', 'O20'),
    ('R5.1', 'O21'),
    ('R5.2', 'O22'),
    ('R5.3', 'O23'),
    ('R6.1', 'O24'),
    ('R6.2', 'O25'),
    ('R7.1', 'O26'),
    ('R7.2', 'O27'),
    ('R7.3', 'O28'),
    ('R8.1', 'O29'),
    ('R8.2', 'O30'),
    ('R8.3', 'O31'),
    ('R8.4', 'O32'),
    ('R9.1', 'O33'),
    ('R9.2', 'O34'),
    ('R9.3', 'O35'),
    ('R9.4', 'O36'),
    ('R9.5', 'O37'),
    ('R9.6', 'O38'),
    ('R9.7', 'O39'),
    ('R9.8', 'O40'),
    ('R9.9', 'O41'),
    ('R10.1', 'O42'),
    ('R10.2', 'O43'),
    ('R10.3', 'O44'),
    ('R10.4', 'O45'),
    ('R10.5', 'O46'),
    ('R11.1', 'O47'),
    ('R11.2', 'O48'),
    ('R11.3', 'O49'),
    ('R11.4', 'O50'),
)


def _values(pairs) -> str:
    return ", ".join(f"('{a}', '{b}')" for a, b in pairs)


def _rename(pairs) -> None:
    # An inline table, not a temporary one: the module's role may not create temporary tables
    # in a project database.
    renames = f"(VALUES {_values(pairs)}) AS r (old_id, new_id)"
    op.execute(f"""
        UPDATE control_objectives.mapped_objective m SET objective_id = r.new_id
          FROM {renames} WHERE m.objective_id = r.old_id""")
    op.execute(f"""
        UPDATE control_objectives.objective_selection s SET objective_ids = coalesce((
            SELECT array_agg(coalesce(r.new_id, o) ORDER BY
                     CASE WHEN coalesce(r.new_id, o) ~ '^O[0-9]+$' THEN substr(coalesce(r.new_id, o), 2)::int END,
                     coalesce(r.new_id, o))
              FROM unnest(s.objective_ids) o LEFT JOIN {renames} ON r.old_id = o), '{{}}')""")


def upgrade() -> None:
    _rename(RENAMES)


def downgrade() -> None:
    _rename(tuple((b, a) for a, b in RENAMES))
