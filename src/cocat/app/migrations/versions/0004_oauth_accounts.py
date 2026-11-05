"""oauth accounts

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-05

"""

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("oauth_account"):
        # A crash after the tables but before the version is recorded leaves them
        # in place: finish the job instead of failing to start.
        return
    op.create_table(
        "oauth_account",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("user_id", sa.CHAR(36), nullable=False),
        sa.Column("oauth_name", sa.String(length=100), nullable=False),
        sa.Column("access_token", sa.String(length=1024), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=True),
        sa.Column("refresh_token", sa.String(length=1024), nullable=True),
        sa.Column("account_id", sa.String(length=320), nullable=False),
        sa.Column("account_email", sa.String(length=320), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="cascade"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("oauth_name", "account_id"),
    )
    op.create_index("ix_oauth_account_oauth_name", "oauth_account", ["oauth_name"])
    op.create_index("ix_oauth_account_account_id", "oauth_account", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_oauth_account_account_id", table_name="oauth_account")
    op.drop_index("ix_oauth_account_oauth_name", table_name="oauth_account")
    op.drop_table("oauth_account")
