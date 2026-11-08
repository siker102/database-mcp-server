"""Loading the Titanic CSV into the table ``passengers``.

Every row is validated with Pydantic before anything is written; an invalid row aborts the
whole seed with the row number, so a broken file never ends up half-loaded [BSI DEV.2.6.1].
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from titanic_mcp.db.schema import passengers

EXPECTED_COLUMNS = (
    "PassengerId",
    "Survived",
    "Pclass",
    "Name",
    "Sex",
    "Age",
    "SibSp",
    "Parch",
    "Ticket",
    "Fare",
    "Cabin",
    "Embarked",
)


def _empty_to_none(value: object) -> object:
    return None if value == "" else value


Optional = BeforeValidator(_empty_to_none)


class PassengerRow(BaseModel):
    """One validated CSV row, already mapped to the snake_case column names."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    passenger_id: int = Field(alias="PassengerId", ge=1)
    survived: int = Field(alias="Survived", ge=0, le=1)
    pclass: int = Field(alias="Pclass", ge=1, le=3)
    name: str = Field(alias="Name", min_length=1, max_length=200)
    sex: Literal["male", "female"] = Field(alias="Sex")
    age: Annotated[float | None, Optional] = Field(alias="Age", ge=0, le=120)
    sib_sp: int = Field(alias="SibSp", ge=0)
    parch: int = Field(alias="Parch", ge=0)
    ticket: str = Field(alias="Ticket", min_length=1, max_length=50)
    fare: float = Field(alias="Fare", ge=0)
    cabin: Annotated[str | None, Optional] = Field(alias="Cabin", max_length=50)
    embarked: Annotated[Literal["C", "Q", "S"] | None, Optional] = Field(alias="Embarked")


class SeedError(ValueError):
    """The CSV file does not match the expected format."""


def read_rows(path: Path) -> list[PassengerRow]:
    """Read and validate the whole CSV file.

    Raises:
        SeedError: If the header differs from the Kaggle format or a row is invalid.
    """
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != EXPECTED_COLUMNS:
            raise SeedError(f"Unexpected CSV header: {reader.fieldnames}")
        rows: list[PassengerRow] = []
        for line_number, raw in enumerate(reader, start=2):
            try:
                rows.append(PassengerRow.model_validate(raw))
            except ValidationError as exc:
                fields = sorted({str(err["loc"][0]) for err in exc.errors()})
                raise SeedError(f"Invalid row at line {line_number}: fields {fields}") from exc
    return rows


async def seed(conn: AsyncConnection, rows: list[PassengerRow]) -> int:
    """Insert rows idempotently and return the number of newly inserted rows.

    ``ON CONFLICT DO NOTHING`` makes a second run a no-op, so ``db-init`` can run on every
    deployment (Helm hook) without duplicating or changing data.
    """
    if not rows:
        return 0
    before = (await conn.execute(select(func.count()).select_from(passengers))).scalar_one()
    statement = insert(passengers).values([row.model_dump() for row in rows])
    await conn.execute(statement.on_conflict_do_nothing(index_elements=["passenger_id"]))
    after = (await conn.execute(select(func.count()).select_from(passengers))).scalar_one()
    return int(after - before)
