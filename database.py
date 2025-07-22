# database.py
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

from config import DATABASE_URL # Import DATABASE_URL from config

# Database setup
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, index=True)
    ca_address = Column(String, unique=True, index=True, nullable=False)
    buy_mc_usd = Column(Float, nullable=False)
    initial_sol_spent = Column(Float, nullable=False)
    initial_ca_bought = Column(Float, nullable=False)
    current_ca_held = Column(Float, nullable=False, default=0.0)
    realized_pnl_sol = Column(Float, nullable=False, default=0.0)
    unrealized_pnl_sol = Column(Float, nullable=False, default=0.0)
    status = Column(String, default="active") # active, completed, cancelled
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    trade_id = Column(Integer, index=True, nullable=False) # Foreign key to Trade
    tx_type = Column(String, nullable=False) # 'buy', 'sell_auto', 'sell_manual', 'pnl_transfer'
    sol_amount = Column(Float, nullable=False) # SOL spent for buy, SOL received for sell
    ca_amount = Column(Float, nullable=False) # CA bought for buy, CA sold for sell
    market_cap_at_tx = Column(Float) # Market cap at the time of transaction
    price_at_tx_usd = Column(Float) # Price per token in USD at tx
    pnl_realized_this_tx = Column(Float, default=0.0) # PnL specifically for this transaction if it's a sell
    tx_signature = Column(String) # Solana transaction signature
    created_at = Column(DateTime, default=datetime.utcnow)

# Function to get a database session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Create tables
def create_db_and_tables():
    Base.metadata.create_all(bind=engine)

if __name__ == "__main__":
    create_db_and_tables()
    print("Database tables created!")