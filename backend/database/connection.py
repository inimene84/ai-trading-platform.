from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get the backend directory path
BACKEND_DIR = Path(__file__).parent.parent

# Database configuration - prefer PostgreSQL from env, fallback to SQLite
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    # PostgreSQL connection
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10, max_overflow=20)
else:
    # SQLite fallback
    DATABASE_PATH = BACKEND_DIR / "hedge_fund.db"
    DATABASE_URL = f"sqlite:///{DATABASE_PATH}"
    engine = create_engine(
        DATABASE_URL, 
        connect_args={"check_same_thread": False},
        pool_size=50,
        max_overflow=50,
        pool_timeout=60
    )

# Create SessionLocal class
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create Base class for models
Base = declarative_base()


def init_db_schema():
    """Ensure tables and additive columns exist without deleting data."""
    from . import models  # noqa
    Base.metadata.create_all(bind=engine)
    if "sqlite" in str(engine.url):
        from sqlalchemy import text
        with engine.connect() as conn:
            try:
                res = conn.execute(text("PRAGMA table_info(trades);")).fetchall()
                cols = {row[1] for row in res}
                if "broker" not in cols:
                    conn.execute(text("ALTER TABLE trades ADD COLUMN broker VARCHAR(30) DEFAULT 'binance_futures';"))
                if "broker_order_id" not in cols:
                    conn.execute(text("ALTER TABLE trades ADD COLUMN broker_order_id VARCHAR(100);"))
                if "broker_position_id" not in cols:
                    conn.execute(text("ALTER TABLE trades ADD COLUMN broker_position_id VARCHAR(100);"))
                if "broker_account_id" not in cols:
                    conn.execute(text("ALTER TABLE trades ADD COLUMN broker_account_id VARCHAR(50);"))
                if "broker_metadata" not in cols:
                    conn.execute(text("ALTER TABLE trades ADD COLUMN broker_metadata JSON;"))
                conn.commit()
            except Exception:
                pass


# Dependency for FastAPI
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
