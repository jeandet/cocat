"""verify existing users

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-05

"""

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Accounts created before email verification existed never got a chance to
    # confirm their address, so they are trusted instead of being locked out the
    # day a mailer is configured.
    op.execute('UPDATE "user" SET is_verified = 1')


def downgrade() -> None:
    # Which accounts were verified before this migration cannot be recovered, so
    # the flag is left alone: un-verifying everybody would lock out users.
    pass
