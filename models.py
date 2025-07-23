# models.py
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean
from sqlalchemy.sql import func
from sqlalchemy.orm import declarative_base # For SQLAlchemy 2.0+

# For SQLAlchemy 1.x, you might use:
# from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class TradeEntry(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, index=True)
    ca_address = Column(String, index=True, nullable=False, unique=True) # Contract Address
    buy_tx_signature = Column(String, unique=True, nullable=True)
    buy_time = Column(DateTime(timezone=True), server_default=func.now())
    buy_price_sol = Column(Float, nullable=False) # SOL amount spent
    ca_amount_bought = Column(Float, nullable=False) # Amount of CA token bought
    target_mc_usd = Column(Float, nullable=False) # Target market cap in USD for monitoring
    percent_of_wallet = Column(Float, nullable=False) # % of wallet used for this trade

    sell_tx_signature = Column(String, unique=True, nullable=True)
    sell_time = Column(DateTime(timezone=True), nullable=True)
    sell_price_sol = Column(Float, nullable=True) # SOL amount received from sell
    ca_amount_sold = Column(Float, nullable=True) # Amount of CA token sold (should be equal to ca_amount_bought for full sell)

    # Current status: ACTIVE, SOLD, CANCELED
    status = Column(String, default="ACTIVE", nullable=False)

    # For PnL calculation
    initial_sol_value = Column(Float, nullable=False) # SOL value at time of purchase
    final_sol_value = Column(Float, nullable=True) # SOL value at time of sale
    realized_pnl = Column(Float, nullable=True) # PnL in SOL (final_sol_value - initial_sol_value)
    pnl_transferred = Column(Boolean, default=False) # Has the PnL been transferred to PNL_WALLET_ADDRESS?

    # Auto-sell trigger related
    auto_sell_triggered = Column(Boolean, default=False)
    # Add other auto-sell specific fields if needed, e.g., profit targets, stop loss

    def __repr__(self):
        return (f"<TradeEntry(id={self.id}, ca_address='{self.ca_address}', "
                f"status='{self.status}', buy_time='{self.buy_time}')>")