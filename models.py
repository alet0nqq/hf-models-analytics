# models.py
"""Нормализованная схема SQLite для аналитики моделей Hugging Face Hub.

Три связанные таблицы (1:M):
    authors  — авторы/организации (вендоры);
    licenses — справочник лицензий с готовой категорией (open / copyleft /
               restricted / noncommercial / other);
    models   — сами модели с внешними ключами на авторов и лицензии.

Категория лицензии вычисляется один раз при создании строки справочника,
чтобы аналитические запросы не повторяли классификацию на лету.
"""
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Author(Base):
    __tablename__ = "authors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)

    models: Mapped[List["Model"]] = relationship(back_populates="author")


class License(Base):
    __tablename__ = "licenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(30), nullable=False, index=True)

    models: Mapped[List["Model"]] = relationship(back_populates="license")


class Model(Base):
    __tablename__ = "models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[str] = mapped_column(String(512), unique=True, nullable=False, index=True)

    author_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("authors.id", ondelete="SET NULL"), index=True
    )
    license_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("licenses.id", ondelete="SET NULL"), index=True
    )

    author: Mapped[Optional["Author"]] = relationship(back_populates="models")
    license: Mapped[Optional["License"]] = relationship(back_populates="models")

    pipeline_tag: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    downloads: Mapped[int] = mapped_column(Integer, default=0, index=True)
    likes: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    tags_json: Mapped[Optional[str]] = mapped_column(Text)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now()
    )


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "hf_models.db"

engine = create_engine(f"sqlite:///{DB_PATH}")


def init_db() -> None:
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode = WAL;")
        conn.exec_driver_sql("PRAGMA foreign_keys = ON;")
    Base.metadata.create_all(engine)


if __name__ == "__main__":
    init_db()
    print("Создана база данных", DB_PATH)
