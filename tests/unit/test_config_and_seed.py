from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from database_mcp.cli import main
from database_mcp.config import DatabaseSettings, ServerSettings
from database_mcp.db.seed import SeedError, read_rows
from tests.conftest import DATA_FILE

ENV = {
    "DBMCP_DB_APP_PASSWORD": "app-secret-value",
    "DBMCP_DB_SQL_PASSWORD": "y",
    "DBMCP_API_KEYS_FILE": "keys.json",
}


def test_server_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    settings = ServerSettings()
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert "app-secret-value" not in repr(settings)  # secrets are masked


def test_missing_secret_stops_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DBMCP_DB_APP_PASSWORD", raising=False)
    monkeypatch.setenv("DBMCP_DB_SQL_PASSWORD", "y")
    monkeypatch.setenv("DBMCP_API_KEYS_FILE", "k.json")
    with pytest.raises(ValidationError):
        ServerSettings()


def test_database_defaults_to_verify_full(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DBMCP_DB_HOST", "db")
    assert DatabaseSettings().sslmode == "verify-full"


def test_invalid_sslmode_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DBMCP_DB_HOST", "db")
    monkeypatch.setenv("DBMCP_DB_SSLMODE", "prefer")  # libpq's silent downgrade mode
    with pytest.raises(ValidationError):
        DatabaseSettings()


def test_cli_reports_invalid_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DBMCP_DB_OWNER_PASSWORD", raising=False)
    monkeypatch.delenv("DBMCP_DB_HOST", raising=False)
    assert main(["db-init"]) == 2


def test_csv_has_891_valid_rows() -> None:
    rows = read_rows(DATA_FILE)
    assert len(rows) == 891
    assert sum(1 for r in rows if r.age is None) == 177
    assert sum(1 for r in rows if r.cabin is None) == 687


def test_wrong_header_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("a,b\n1,2\n")
    with pytest.raises(SeedError, match="header"):
        read_rows(path)


def test_invalid_row_reports_line(tmp_path: Path) -> None:
    lines = DATA_FILE.read_text().splitlines()[:3]
    lines[2] = lines[2].replace(",female,", ",robot,")
    path = tmp_path / "bad.csv"
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(SeedError, match="line 3"):
        read_rows(path)
