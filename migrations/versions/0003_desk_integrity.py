"""Enforce desk identity when floor is unknown and valid location values."""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    duplicates = connection.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM desks GROUP BY floor, number HAVING count(*) > 1)"
    ))
    invalid_locations = connection.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM desks WHERE floor < 0 OR section <= 0)"
    ))
    if duplicates or invalid_locations:
        raise RuntimeError(
            "Desk integrity migration requires unique (floor, number), floor >= 0 and section > 0. "
            "Resolve existing invalid desks explicitly before retrying; no data has been removed."
        )
    op.drop_constraint("uq_desks_floor_number", "desks", type_="unique")
    op.create_unique_constraint(
        "uq_desks_floor_number", "desks", ["floor", "number"],
        postgresql_nulls_not_distinct=True,
    )
    op.create_check_constraint("ck_desks_floor", "desks", "floor IS NULL OR floor >= 0")
    op.create_check_constraint("ck_desks_section", "desks", "section IS NULL OR section > 0")


def downgrade():
    op.drop_constraint("ck_desks_section", "desks", type_="check")
    op.drop_constraint("ck_desks_floor", "desks", type_="check")
    op.drop_constraint("uq_desks_floor_number", "desks", type_="unique")
    op.create_unique_constraint("uq_desks_floor_number", "desks", ["floor", "number"])
