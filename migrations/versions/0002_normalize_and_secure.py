"""Normalize features and add verified profiles, assets and audit history.

Legacy feature IDs are validated before conversion; invalid data aborts the
transaction so an operator can correct it without losing the original JSON.
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("users", sa.Column("phone", sa.String(40), nullable=False, server_default=""))
    op.add_column("users", sa.Column("avatar_url", sa.String(1024), nullable=True))
    op.add_column("equipment", sa.Column("model", sa.String(128), nullable=False, server_default=""))
    op.add_column("equipment", sa.Column("inventory_number", sa.String(128), nullable=True))
    op.create_unique_constraint("uq_equipment_inventory_number", "equipment", ["inventory_number"])
    op.create_table("desk_feature_links",
        sa.Column("desk_id", sa.Integer(), sa.ForeignKey("desks.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("feature_id", sa.Integer(), sa.ForeignKey("desk_features.id", ondelete="RESTRICT"), primary_key=True))
    op.create_index("ix_desk_feature_links_feature_id", "desk_feature_links", ["feature_id"])
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM desks WHERE jsonb_typeof(features) <> 'array') THEN
            RAISE EXCEPTION 'Legacy desk features must be arrays; correct before migrating';
        END IF;
        IF EXISTS (SELECT 1 FROM desks CROSS JOIN LATERAL jsonb_array_elements_text(features) f(value)
                   WHERE value !~ '^[0-9]{1,9}$') THEN
            RAISE EXCEPTION 'Legacy desk features contain invalid IDs; correct before migrating';
        END IF;
        IF EXISTS (SELECT 1 FROM desks CROSS JOIN LATERAL jsonb_array_elements_text(features) f(value)
                   LEFT JOIN desk_features df ON df.id=value::integer WHERE df.id IS NULL) THEN
            RAISE EXCEPTION 'Legacy desk features reference missing IDs; correct before migrating';
        END IF;
    END $$""")
    op.execute("""INSERT INTO desk_feature_links(desk_id,feature_id)
        SELECT DISTINCT desks.id,f.value::integer FROM desks
        CROSS JOIN LATERAL jsonb_array_elements_text(features) f(value)""")
    op.drop_index("ix_desks_features", table_name="desks")
    op.drop_column("desks", "features")
    # This was a duplicated list of IDs; bookings remains the source of truth.
    op.drop_column("users", "current_bookings")
    op.create_check_constraint("ck_users_role", "users", "role IN ('employee','it_admin','office_manager')")
    op.create_check_constraint("ck_desks_number", "desks", "number > 0")
    op.create_check_constraint("ck_desks_sockets", "desks", "sockets IS NULL OR sockets >= 0")
    op.create_check_constraint("ck_bookings_duration", "bookings", "duration > interval '0 seconds' AND duration <= interval '24 hours'")
    op.create_check_constraint("ck_equipment_status", "equipment", "status IN ('in_stock','assigned','in_repair','written_off')")
    op.create_check_constraint("ck_tickets_status", "tickets", "status IN ('new','in_progress','resolved','closed')")
    op.create_table("email_verification_codes",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False))
    op.create_index("ix_email_verification_codes_expires_at", "email_verification_codes", ["expires_at"])
    op.create_table("notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.String(1024), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_table("ticket_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_ticket_events_ticket_id", "ticket_events", ["ticket_id"])
    op.create_table("uploads",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_uploads_user_id", "uploads", ["user_id"])


def downgrade():
    # Reconstruct the legacy derived fields before dropping the normalized data.
    from sqlalchemy.dialects.postgresql import JSONB
    op.add_column("desks", sa.Column("features", JSONB(), nullable=False, server_default="[]"))
    op.execute("""UPDATE desks d SET features = COALESCE(
        (SELECT jsonb_agg(feature_id ORDER BY feature_id) FROM desk_feature_links l WHERE l.desk_id=d.id), '[]'::jsonb)""")
    op.add_column("users", sa.Column("current_bookings", JSONB(), nullable=False, server_default="[]"))
    op.execute("""UPDATE users u SET current_bookings = COALESCE(
        (SELECT jsonb_agg(id ORDER BY id) FROM bookings b WHERE b.user_id=u.id AND NOT b.is_cancelled), '[]'::jsonb)""")
    for name in ("uploads", "ticket_events", "notifications", "email_verification_codes", "desk_feature_links"):
        op.drop_table(name)
    for table, constraint in (("tickets", "ck_tickets_status"), ("equipment", "ck_equipment_status"),
                              ("bookings", "ck_bookings_duration"), ("desks", "ck_desks_sockets"),
                              ("desks", "ck_desks_number"), ("users", "ck_users_role")):
        op.drop_constraint(constraint, table, type_="check")
    op.create_index("ix_desks_features", "desks", ["features"], postgresql_using="gin")
    op.drop_constraint("uq_equipment_inventory_number", "equipment", type_="unique")
    op.drop_column("equipment", "inventory_number")
    op.drop_column("equipment", "model")
    for name in ("avatar_url", "phone", "is_verified"):
        op.drop_column("users", name)
