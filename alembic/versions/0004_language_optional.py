"""Allow first-time users to choose a language on /start."""
from alembic import op
import sqlalchemy as sa

revision = "0004_language_optional"
down_revision = "0003_departure_period"
branch_labels = None
depends_on = None

def upgrade() -> None:
    """Allow new users to choose a language after their first interaction."""
    op.alter_column("users", "language", existing_type=sa.String(length=2), nullable=True, server_default=None)

def downgrade() -> None:
    """Restore the required Russian language default for existing users."""
    op.execute("UPDATE users SET language = 'ru' WHERE language IS NULL")
    op.alter_column("users", "language", existing_type=sa.String(length=2), nullable=False, server_default="ru")
