"""Store passenger interface language."""
from alembic import op
import sqlalchemy as sa

revision = "0002_user_language"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

def upgrade() -> None:
    """Add the user's language preference column."""
    op.add_column("users", sa.Column("language", sa.String(length=2), nullable=False, server_default="ru"))

def downgrade() -> None:
    """Remove the user language preference column."""
    op.drop_column("users", "language")
