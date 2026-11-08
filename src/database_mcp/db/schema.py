"""Table definition and column groups of the Titanic dataset.

The schema is defined once with SQLAlchemy Core (no ORM): queries stay explicit SQL
expressions, which keeps them easy to read and review.
"""

from __future__ import annotations

from sqlalchemy import Column, Float, Integer, MetaData, SmallInteger, String, Table

metadata = MetaData()

passengers = Table(
    "passengers",
    metadata,
    Column("passenger_id", Integer, primary_key=True, autoincrement=False),
    Column("survived", SmallInteger, nullable=False),
    Column("pclass", SmallInteger, nullable=False),
    Column("name", String(200), nullable=False),
    Column("sex", String(10), nullable=False),
    Column("age", Float, nullable=True),
    Column("sib_sp", SmallInteger, nullable=False),
    Column("parch", SmallInteger, nullable=False),
    Column("ticket", String(50), nullable=False),
    Column("fare", Float, nullable=False),
    Column("cabin", String(50), nullable=True),
    Column("embarked", String(1), nullable=True),
)

PII_COLUMNS: tuple[str, ...] = ("name", "ticket", "cabin")
"""Columns treated as personal data (see ADR 0005)."""

PUBLIC_COLUMNS: tuple[str, ...] = tuple(
    c.name for c in passengers.columns if c.name not in PII_COLUMNS
)
"""Columns of the view ``passengers_public``."""

PUBLIC_VIEW = "passengers_public"

# Same columns as the view, so the repository can select from it with SQLAlchemy Core.
passengers_public = Table(
    PUBLIC_VIEW,
    MetaData(),
    *(Column(c.name, c.type) for c in passengers.columns if c.name not in PII_COLUMNS),
)
