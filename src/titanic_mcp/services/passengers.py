"""Logic of the fixed tools: limits, PII rules, result shapes.

The PII decision is made once per call (``include_pii``) and decides which table the repository
reads; output models without PII fields make the result shape match [ADR 0005].
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from titanic_mcp.db.repository import GROUPABLE_COLUMNS, PassengerFilter, PassengerRepository
from titanic_mcp.db.schema import PII_COLUMNS

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
"""Page size bounds: big answers fill the LLM context and load the database for no gain."""

GroupColumn = Literal["pclass", "sex", "embarked", "survived"]


class ServiceError(Exception):
    """A request the caller can fix; the message is safe to show to the agent."""


class PassengerPublic(BaseModel):
    """A passenger without personal fields."""

    passenger_id: int
    survived: int
    pclass: int
    sex: str
    age: float | None
    sib_sp: int
    parch: int
    fare: float
    embarked: str | None


class Passenger(PassengerPublic):
    """A passenger including personal fields (scope ``pii``)."""

    name: str
    ticket: str
    cabin: str | None


class PassengerResult(BaseModel):
    """Result of ``get_passenger``; personal fields only with scope ``pii``."""

    passenger: Passenger | PassengerPublic


class SearchResult(BaseModel):
    """One page of ``search_passengers``."""

    total: int = Field(description="Number of all matching passengers.")
    limit: int
    offset: int
    passengers: list[Passenger | PassengerPublic]


class StatisticsRow(BaseModel):
    """Aggregates of one group."""

    group: dict[str, Any] = Field(description="Values of the group_by columns for this row.")
    count: int
    survival_rate: float | None
    avg_age: float | None
    avg_fare: float | None


class StatisticsResult(BaseModel):
    """Result of ``get_statistics``."""

    group_by: list[str]
    rows: list[StatisticsRow]


def _to_model(row: dict[str, Any], include_pii: bool) -> Passenger | PassengerPublic:
    if include_pii:
        return Passenger.model_validate(row)
    # Defense in depth: even if a PII column slipped into the row, the public model drops it.
    return PassengerPublic.model_validate({k: v for k, v in row.items() if k not in PII_COLUMNS})


class PassengerService:
    """Search, lookup and statistics on the passengers table."""

    def __init__(self, repository: PassengerRepository) -> None:
        self._repo = repository

    async def search(
        self, flt: PassengerFilter, *, limit: int, offset: int, include_pii: bool
    ) -> SearchResult:
        """Search passengers. ``name_contains`` needs ``include_pii``."""
        if flt.name_contains is not None and not include_pii:
            raise ServiceError("name_contains requires the 'pii' scope.")
        if not 1 <= limit <= MAX_LIMIT:
            raise ServiceError(f"limit must be between 1 and {MAX_LIMIT}.")
        if offset < 0:
            raise ServiceError("offset must not be negative.")
        rows, total = await self._repo.search(
            flt, limit=limit, offset=offset, include_pii=include_pii
        )
        return SearchResult(
            total=total,
            limit=limit,
            offset=offset,
            passengers=[_to_model(r, include_pii) for r in rows],
        )

    async def get(self, passenger_id: int, *, include_pii: bool) -> PassengerResult:
        """Return one passenger; personal fields only with ``include_pii``."""
        row = await self._repo.get(passenger_id, include_pii=include_pii)
        if row is None:
            raise ServiceError(f"No passenger with passenger_id {passenger_id}.")
        return PassengerResult(passenger=_to_model(row, include_pii))

    async def statistics(self, flt: PassengerFilter, group_by: list[str]) -> StatisticsResult:
        """Aggregate per group; aggregates never contain personal data."""
        if flt.name_contains is not None:
            raise ServiceError("name_contains is not supported for statistics.")
        unknown = [g for g in group_by if g not in GROUPABLE_COLUMNS]
        if unknown:
            raise ServiceError(f"Cannot group by {unknown}; allowed: {list(GROUPABLE_COLUMNS)}.")
        if len(set(group_by)) != len(group_by):
            raise ServiceError("group_by must not contain duplicates.")
        rows = await self._repo.statistics(flt, group_by)
        return StatisticsResult(
            group_by=group_by,
            rows=[
                StatisticsRow(
                    group={g: r[g] for g in group_by},
                    count=r["count"],
                    survival_rate=_round(r["survival_rate"]),
                    avg_age=_round(r["avg_age"]),
                    avg_fare=_round(r["avg_fare"]),
                )
                for r in rows
            ],
        )


def _round(value: Any) -> float | None:
    return None if value is None else round(float(value), 4)
