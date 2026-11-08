"""Registration of all MCP tools.

Descriptions are written for the LLM: it picks tools by these texts. Inputs are validated by the
SDK against the annotated types and bounds before the function runs [BSI DEV.2.6.1].
"""

from __future__ import annotations

from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from database_mcp.auth import Scope
from database_mcp.db.repository import PassengerFilter
from database_mcp.services import dataset
from database_mcp.services.dataset import DatasetDescription
from database_mcp.services.passengers import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    GroupColumn,
    PassengerResult,
    PassengerService,
    SearchResult,
    StatisticsResult,
)
from database_mcp.services.sql_runner import DEFAULT_ROWS, MAX_ROWS, SqlResult, SqlRunner
from database_mcp.tools.common import run_tool

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)

Survived = Annotated[int | None, Field(ge=0, le=1, description="0 = died, 1 = survived.")]
Pclass = Annotated[int | None, Field(ge=1, le=3, description="Ticket class 1, 2 or 3.")]
Sex = Annotated[Literal["male", "female"] | None, Field(description="Sex.")]
Embarked = Annotated[
    Literal["C", "Q", "S"] | None,
    Field(description="Port: C = Cherbourg, Q = Queenstown, S = Southampton."),
]
Age = Annotated[float | None, Field(ge=0, le=120)]
Fare = Annotated[float | None, Field(ge=0, le=10000)]


def register_tools(mcp: MCPServer, passengers: PassengerService, sql: SqlRunner) -> None:
    """Register the five tools on ``mcp``."""

    @mcp.tool(
        name="describe_dataset",
        description=(
            "Describe the Titanic dataset: columns, types, meaning and value ranges. "
            "Call this first to learn which filters and columns exist. Needs scope 'read'."
        ),
        annotations=READ_ONLY,
    )
    async def describe_dataset() -> DatasetDescription:
        return await run_tool("describe_dataset", {}, lambda _p: _describe())

    @mcp.tool(
        name="search_passengers",
        description=(
            "Search passengers with optional filters and pagination (limit/offset). Returns "
            "matching rows ordered by passenger_id and the total number of matches. Names, "
            "tickets and cabins are only included with scope 'pii'; name_contains also needs "
            "'pii'. Needs scope 'read'."
        ),
        annotations=READ_ONLY,
    )
    async def search_passengers(
        survived: Survived = None,
        pclass: Pclass = None,
        sex: Sex = None,
        embarked: Embarked = None,
        min_age: Age = None,
        max_age: Age = None,
        min_fare: Fare = None,
        max_fare: Fare = None,
        name_contains: Annotated[
            str | None,
            Field(min_length=2, max_length=50, description="Case-insensitive part of the name."),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
        offset: Annotated[int, Field(ge=0, le=10_000)] = 0,
    ) -> SearchResult:
        params = {
            "survived": survived,
            "pclass": pclass,
            "sex": sex,
            "embarked": embarked,
            "min_age": min_age,
            "max_age": max_age,
            "min_fare": min_fare,
            "max_fare": max_fare,
            "name_contains": name_contains,
        }
        flt = PassengerFilter(**params)  # type: ignore[arg-type]
        return await run_tool(
            "search_passengers",
            {**params, "limit": limit, "offset": offset},
            lambda p: passengers.search(
                flt, limit=limit, offset=offset, include_pii=p.has(Scope.PII)
            ),
        )

    @mcp.tool(
        name="get_passenger",
        description=(
            "Get one passenger by passenger_id (1-891). Personal fields (name, ticket, cabin) "
            "only with scope 'pii'. Needs scope 'read'."
        ),
        annotations=READ_ONLY,
    )
    async def get_passenger(
        passenger_id: Annotated[int, Field(ge=1, le=100_000)],
    ) -> PassengerResult:
        return await run_tool(
            "get_passenger",
            {"passenger_id": passenger_id},
            lambda p: passengers.get(passenger_id, include_pii=p.has(Scope.PII)),
        )

    @mcp.tool(
        name="get_statistics",
        description=(
            "Aggregate passengers: count, survival rate (0-1), average age and average fare, "
            "optionally grouped by pclass, sex, embarked and/or survived and filtered like "
            "search_passengers. Example: survival rate by class and sex. Needs scope 'read'."
        ),
        annotations=READ_ONLY,
    )
    async def get_statistics(
        group_by: Annotated[list[GroupColumn], Field(max_length=4)] = [],  # noqa: B006
        survived: Survived = None,
        pclass: Pclass = None,
        sex: Sex = None,
        embarked: Embarked = None,
        min_age: Age = None,
        max_age: Age = None,
        min_fare: Fare = None,
        max_fare: Fare = None,
    ) -> StatisticsResult:
        params = {
            "survived": survived,
            "pclass": pclass,
            "sex": sex,
            "embarked": embarked,
            "min_age": min_age,
            "max_age": max_age,
            "min_fare": min_fare,
            "max_fare": max_fare,
        }
        flt = PassengerFilter(**params)  # type: ignore[arg-type]
        return await run_tool(
            "get_statistics",
            {**params, "group_by": list(group_by)},
            lambda _p: passengers.statistics(flt, list(group_by)),
        )

    @mcp.tool(
        name="run_sql",
        description=(
            "Run one read-only PostgreSQL SELECT on the view passengers_public (columns: "
            "passenger_id, survived, pclass, sex, age, sib_sp, parch, fare, embarked). "
            "Only aggregate, math, string and date functions are allowed; no other tables, no "
            "writes. Results are capped at 'limit' rows (max 500); 'truncated' tells whether "
            "more rows existed. Use get_statistics for common aggregates. Needs scope 'sql'."
        ),
        annotations=READ_ONLY,
    )
    async def run_sql(
        query: Annotated[str, Field(min_length=1, max_length=5000)],
        limit: Annotated[int, Field(ge=1, le=MAX_ROWS)] = DEFAULT_ROWS,
    ) -> SqlResult:
        # The query text is audited in full: it is the request, not result data.
        return await run_tool(
            "run_sql", {"query": query, "limit": limit}, lambda _p: sql.run(query, limit=limit)
        )


async def _describe() -> DatasetDescription:
    return dataset.describe()
