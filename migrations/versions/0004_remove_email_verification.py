"""Remove email verification while preserving every existing user account."""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_table("email_verification_codes")
    op.drop_column("users", "is_verified")


def downgrade():
    # Restored accounts remain usable; old verification codes are not recoverable.
    op.add_column("users", sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table(
        "email_verification_codes",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_email_verification_codes_expires_at", "email_verification_codes", ["expires_at"])
