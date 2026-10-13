"""Store selected departure interval."""
from alembic import op
import sqlalchemy as sa

revision = "0003_departure_period"
down_revision = "0002_user_language"
branch_labels = None
depends_on = None

def upgrade() -> None:
    """Store the selected departure interval on each order."""
    op.add_column("orders", sa.Column("departure_period", sa.String(length=20), nullable=False, server_default="exact"))

def downgrade() -> None:
    """Remove the departure interval field from orders."""
    op.drop_column("orders", "departure_period")
