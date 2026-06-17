"""Initial schema — all tables

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-17 00:00:00.000000
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Tables are already managed by SQLAlchemy Base.metadata.create_all()
    # in db_init.py. This initial migration acts as a baseline checkpoint.
    # Future schema changes should use op.add_column / op.create_table etc.
    pass


def downgrade() -> None:
    # Intentionally empty for the baseline migration
    pass
