"""Validation of free SQL for ``run_sql`` (layer 1 of the defense in depth).

The validator rejects early and explains why, so the agent can correct its query. It is *not*
the hard boundary: that is the database role ``titanic_sql``, which may only read the view
``passengers_public`` [BSI DEV.2.6, DEV.2.6.1]. Everything is an allowlist (statement types,
tables, functions) because a blocklist over hundreds of PostgreSQL functions would never be
complete.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

MAX_QUERY_LENGTH = 5000
"""Upper bound for the query text; longer queries are no realistic analysis question."""

ALLOWED_TABLES = frozenset({"passengers_public"})

# Class names of parsed sqlglot function nodes (aggregates, math, string, date, window, CASE).
ALLOWED_FUNCTIONS = frozenset(
    {
        "Count", "Sum", "Avg", "Min", "Max", "Stddev", "StddevPop", "StddevSamp", "Variance",
        "VariancePop", "PercentileCont", "PercentileDisc", "Median", "CountIf",
        "Round", "Abs", "Floor", "Ceil", "Sqrt", "Pow", "Ln", "Log", "Exp", "Sign", "Greatest",
        "Least", "Nullif", "Coalesce", "Cast", "TryCast", "Case", "If",
        "Lower", "Upper", "Length", "Substring", "Concat", "Trim", "Left", "Right",
        "Extract", "CurrentDate", "CurrentTimestamp", "DateTrunc", "TimestampTrunc",
        "RowNumber", "Rank", "DenseRank", "Ntile", "Lag", "Lead", "FirstValue", "LastValue",
        "Filter", "Distinct",
    }
)  # fmt: skip

# Data types allowed in casts and typed literals. Object identifier types such as regclass,
# regrole or oid would let a query probe the catalog ("does role X exist?"), so they are out.
ALLOWED_TYPES = frozenset(
    {
        "SMALLINT", "INT", "BIGINT", "DECIMAL", "FLOAT", "DOUBLE", "BOOLEAN",
        "TEXT", "VARCHAR", "CHAR", "DATE", "TIME", "TIMESTAMP", "TIMESTAMPTZ", "INTERVAL",
    }
)  # fmt: skip

# Node types that change data, schema, rights or session state. Rejected anywhere in the tree,
# which also catches data-modifying CTEs (WITH x AS (DELETE ...)).
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Copy,
    exp.Set,
    exp.Grant,
    exp.Command,
    exp.Into,
    exp.Lock,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
)

_QUERY_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)


class SqlRejectedError(ValueError):
    """The query is not allowed; the message tells the agent why."""


def _function_name(node: exp.Func) -> str:
    return node.name.lower() if isinstance(node, exp.Anonymous) else type(node).__name__


def _identifier(node: exp.Identifier) -> str:
    """Name as PostgreSQL resolves it: unquoted names fold to lower case, quoted ones are exact."""
    return str(node.this) if node.quoted else str(node.this).lower()


def _cte_name(cte: exp.CTE) -> str:
    return _identifier(cte.args["alias"].this)


def _visible_cte_names(table: exp.Table) -> set[str]:
    """Names of the CTEs that ``table`` can refer to, following PostgreSQL's scoping rules.

    A CTE is visible in the query that defines it and in that query's subqueries, never outside.
    Inside a WITH list a CTE only sees the CTEs before it (with RECURSIVE: all of them). Without
    these rules a CTE named like a catalog view in one scope would whitelist the real catalog
    view in another scope, e.g. ``SELECT * FROM (WITH pg_roles AS (SELECT 1) SELECT 1) x,
    pg_roles`` (regression test in tests/security).
    """
    names: set[str] = set()
    node: exp.Expr = table
    while node.parent is not None:
        parent = node.parent
        if isinstance(parent, exp.With) and isinstance(node, exp.CTE):
            ctes = parent.expressions
            position = next(i for i, cte in enumerate(ctes) if cte is node)
            visible = ctes if parent.args.get("recursive") else ctes[:position]
            names.update(_cte_name(cte) for cte in visible)
        else:
            with_clause = parent.args.get("with_")
            if isinstance(with_clause, exp.With) and node is not with_clause:
                names.update(_cte_name(cte) for cte in with_clause.expressions)
        node = parent
    return names


def validate(query: str) -> str:
    """Validate ``query`` and return it normalised (single statement, no comments).

    Raises:
        SqlRejectedError: With a message that is safe and useful for the agent.
    """
    if not query.strip():
        raise SqlRejectedError("Query is empty.")
    if len(query) > MAX_QUERY_LENGTH:
        raise SqlRejectedError(f"Query is longer than {MAX_QUERY_LENGTH} characters.")
    try:
        statements = [s for s in sqlglot.parse(query, read="postgres") if s is not None]
    except ParseError as exc:
        raise SqlRejectedError("Query could not be parsed as PostgreSQL.") from exc
    if len(statements) != 1:
        raise SqlRejectedError("Exactly one statement is allowed.")
    tree = statements[0]
    if not isinstance(tree, _QUERY_ROOTS):
        raise SqlRejectedError("Only SELECT queries are allowed.")

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise SqlRejectedError(
                f"{type(node).__name__.upper()} is not allowed; only read-only SELECT queries."
            )

    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise SqlRejectedError("Table functions are not allowed.")
        name = _identifier(table.this)
        if table.db or table.catalog:
            raise SqlRejectedError(
                "Schema-qualified tables are not allowed; use passengers_public."
            )
        if name not in ALLOWED_TABLES and name not in _visible_cte_names(table):
            raise SqlRejectedError(f"Table '{name}' is not allowed; only passengers_public.")

    for func in tree.find_all(exp.Func):
        name = _function_name(func)
        if name not in ALLOWED_FUNCTIONS:
            raise SqlRejectedError(f"Function '{name}' is not allowed.")

    for data_type in tree.find_all(exp.DataType):
        kind = data_type.this
        if not isinstance(kind, exp.DataType.Type) or kind.name not in ALLOWED_TYPES:
            raise SqlRejectedError(f"Type '{data_type.sql(dialect='postgres')}' is not allowed.")

    # Without comments: sqlglot would otherwise re-emit them as /* ... */, and PostgreSQL nests
    # block comments, so the executed text could differ from what was validated.
    return tree.sql(dialect="postgres", comments=False)
