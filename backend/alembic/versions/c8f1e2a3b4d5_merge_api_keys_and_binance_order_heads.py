"""merge api_keys and binance_order_id alembic heads

Revision ID: c8f1e2a3b4d5
Revises: d5e78f9a1b2c, a9c3f2b1d4e7
Create Date: 2026-09-05 03:30:00

"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "c8f1e2a3b4d5"
down_revision: Union[str, tuple[str, ...], None] = ("d5e78f9a1b2c", "a9c3f2b1d4e7")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
