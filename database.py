# database.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from models import Base # Import Base from models.py

from config import DATABASE_URL

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def create_db_and_tables():
    """Creates all database tables defined in models.py."""
    Base.metadata.create_all(engine)

def get_db():
    """
    Dependency to get a database session.
    Yields a session and ensures it's closed afterwards.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()