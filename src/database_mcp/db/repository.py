"""All SQL of the fixed tools, in one place.

Queries are built with SQLAlchemy Core, so every user value is a bound parameter, never part of
the SQL text [BSI DEV.2.6]. Callers without ``pii`` read from the view ``passengers_public``:
the personal columns are then not even selected, so a masking bug cannot leak them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, Select, Table, and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from database_mcp.db.schema import passengers, passengers_public

GROUPABLE_COLUMNS: tuple[str, ...] = ("pclass", "sex", "embarked", "survived")


@dataclass(frozen=True)
class PassengerFilter:
    """Filter values of ``search_passengers`` / ``get_statistics``; ``None`` means no filter."""

    survived: int | None = None
    pclass: int | None = None
    sex: str | None = None
    embarked: str | None = None
    min_age: float | None = None
    max_age: float | None = None
    min_fare: float | None = None
    max_fare: float | None = None
    name_contains: str | None = None


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _conditions(table: Table, flt: PassengerFilter) -> list[ColumnElement[bool]]:
    col = table.c
    conds: list[ColumnElement[bool]] = []
    for name in ("survived", "pclass", "sex", "embarked"):
        value = getattr(flt, name)
        if value is not None:
            conds.append(col[name] == value)
    if flt.min_age is not None:
        conds.append(col.age >= flt.min_age)
    if flt.max_age is not None:
        conds.append(col.age <= flt.max_age)
    if flt.min_fare is not None:
        conds.append(col.fare >= flt.min_fare)
    if flt.max_fare is not None:
        conds.append(col.fare <= flt.max_fare)
    if flt.name_contains is not None:
        # Only reachable on the full table; the service checks the pii scope first.
        conds.append(col.name.ilike(f"%{_escape_like(flt.name_contains)}%", escape="\\"))
    return conds


class PassengerRepository:
    """Read-only queries for the fixed tools, executed with the role ``titanic_app``."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @staticmethod
    def _table(include_pii: bool) -> Table:
        return passengers if include_pii else passengers_public

    async def search(
        self, flt: PassengerFilter, *, limit: int, offset: int, include_pii: bool
    ) -> tuple[list[dict[str, Any]], int]:
        """Return one page of matching passengers and the total number of matches."""
        table = self._table(include_pii)
        where = and_(True, *_conditions(table, flt))
        page: Select[Any] = (
            select(table).where(where).order_by(table.c.passenger_id).limit(limit).offset(offset)
        )
        total = select(func.count()).select_from(table).where(where)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(page)).mappings().all()
            count = (await conn.execute(total)).scalar_one()
        return [dict(r) for r in rows], int(count)

    async def get(self, passenger_id: int, *, include_pii: bool) -> dict[str, Any] | None:
        """Return one passenger or ``None``."""
        table = self._table(include_pii)
        async with self._engine.connect() as conn:
            row = (
                (await conn.execute(select(table).where(table.c.passenger_id == passenger_id)))
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def statistics(
        self, flt: PassengerFilter, group_by: Sequence[str]
    ) -> list[dict[str, Any]]:
        """Aggregate count, survival rate, average age and fare per group.

        Always runs on the public view: aggregates never need personal columns.
        """
        table = passengers_public
        groups = [table.c[name] for name in group_by if name in GROUPABLE_COLUMNS]
        stmt = (
            select(
                *groups,
                func.count().label("count"),
                func.avg(case((table.c.survived == 1, 1.0), else_=0.0)).label("survival_rate"),
                func.avg(table.c.age).label("avg_age"),
                func.avg(table.c.fare).label("avg_fare"),
            )
            .where(and_(True, *_conditions(table, flt)))
            .group_by(*groups)
            .order_by(*groups)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [dict(r) for r in rows]

    async def ping(self) -> None:
        """Run a trivial query; raises if the database is unreachable."""
        async with self._engine.connect() as conn:
            await conn.execute(select(1))
