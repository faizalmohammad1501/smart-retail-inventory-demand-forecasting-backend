from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./supply_chain.db")

_is_sqlite = "sqlite" in DATABASE_URL

if _is_sqlite:
    # SQLite: single shared connection, thread-safety delegated to check_same_thread=False
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )

    # Enable WAL mode and busy-timeout for better concurrency under load
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA cache_size=-8000")   # 8 MB page cache
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

else:
    # PostgreSQL / MySQL: connection pool tuned for API workloads
    engine = create_engine(
        DATABASE_URL,
        pool_size=10,           # persistent connections
        max_overflow=20,        # extra connections allowed under burst
        pool_pre_ping=True,     # test connection health before checkout
        pool_recycle=3600,      # recycle connections after 1 hour
        pool_timeout=30,        # raise after 30 s if no connection available
        echo=False,
    )

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    expire_on_commit=False,     # avoid extra SELECT after commit
)

Base = declarative_base()


def get_db():
    """Database session dependency — yields a session and guarantees close."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
