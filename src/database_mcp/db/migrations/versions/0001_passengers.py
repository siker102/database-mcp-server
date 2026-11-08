"""Create table passengers and view passengers_public.

Revision ID: 0001
Revises:
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "passengers",
        sa.Column("passenger_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("survived", sa.SmallInteger(), nullable=False),
        sa.Column("pclass", sa.SmallInteger(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("sex", sa.String(10), nullable=False),
        sa.Column("age", sa.Float(), nullable=True),
        sa.Column("sib_sp", sa.SmallInteger(), nullable=False),
        sa.Column("parch", sa.SmallInteger(), nullable=False),
        sa.Column("ticket", sa.String(50), nullable=False),
        sa.Column("fare", sa.Float(), nullable=False),
        sa.Column("cabin", sa.String(50), nullable=True),
        sa.Column("embarked", sa.String(1), nullable=True),
        sa.CheckConstraint("survived IN (0, 1)", name="ck_passengers_survived"),
        sa.CheckConstraint("pclass IN (1, 2, 3)", name="ck_passengers_pclass"),
        sa.CheckConstraint("sex IN ('male', 'female')", name="ck_passengers_sex"),
        sa.CheckConstraint("embarked IN ('C', 'Q', 'S')", name="ck_passengers_embarked"),
    )
    # Without security_invoker the view runs with the owner's rights, so the role titanic_sql
    # can read it without any right on the base table (and thus never sees PII columns).
    op.execute(
        "CREATE VIEW passengers_public AS "
        "SELECT passenger_id, survived, pclass, sex, age, sib_sp, parch, fare, embarked "
        "FROM passengers"
    )


def downgrade() -> None:
    op.execute("DROP VIEW passengers_public")
    op.drop_table("passengers")
