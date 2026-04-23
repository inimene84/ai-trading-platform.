"""add_binance_order_id_to_trades

Revision ID: a9c3f2b1d4e7
Revises: 3f9a6b7c8d2e
Create Date: 2026-04-20 21:30:00

Adds binance_order_id, exchange, and filled_price columns to the trades table
for Binance Futures order tracking and fill price reconciliation.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a9c3f2b1d4e7'
down_revision = '3f9a6b7c8d2e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add binance_order_id column with index for fast lookup
    op.add_column('trades', sa.Column('binance_order_id', sa.String(50), nullable=True))
    op.create_index('ix_trades_binance_order_id', 'trades', ['binance_order_id'], unique=False)

    # Add exchange column to track which exchange the order was placed on
    op.add_column('trades', sa.Column('exchange', sa.String(20), nullable=True, server_default='binance_futures'))

    # Add filled_price column to track actual fill price vs signal entry price
    op.add_column('trades', sa.Column('filled_price', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('trades', 'filled_price')
    op.drop_column('trades', 'exchange')
    op.drop_index('ix_trades_binance_order_id', table_name='trades')
    op.drop_column('trades', 'binance_order_id')
