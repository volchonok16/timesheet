from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class TimeEntry(Base):
    __tablename__ = "time_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_key: Mapped[str] = mapped_column(String(255), index=True)
    parent_work_item_id: Mapped[int] = mapped_column(Integer, index=True)
    tracking_work_item_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    role: Mapped[str] = mapped_column(String(128))
    activity: Mapped[str] = mapped_column(String(255))
    entry_date: Mapped[date] = mapped_column(Date, index=True)
    hours: Mapped[float] = mapped_column(Float)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_project: Mapped[str | None] = mapped_column(String(512), nullable=True)
    tfs_sync_key: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RecentWorkItem(Base):
    __tablename__ = "recent_work_items"
    __table_args__ = (UniqueConstraint("account_key", "work_item_id", name="uq_recent_account_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_key: Mapped[str] = mapped_column(String(255), index=True)
    work_item_id: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(1024))
    work_item_type: Mapped[str] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(128))
    area_path: Mapped[str] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(32))
    touched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
