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


def test_cli_serve_stops_on_missing_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """``serve`` must not start without its secrets [BSI DEV.4.8]."""
    monkeypatch.delenv("DBMCP_DB_APP_PASSWORD", raising=False)
    monkeypatch.setenv("DBMCP_DB_SQL_PASSWORD", "y")
    monkeypatch.setenv("DBMCP_API_KEYS_FILE", "k.json")
    assert main(["serve"]) == 2


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("DBMCP_PORT", "0"),
        ("DBMCP_PORT", "70000"),
        ("DBMCP_LOG_LEVEL", "TRACE"),
        ("DBMCP_RATE_LIMIT_PER_MINUTE", "0"),
        ("DBMCP_RATE_LIMIT_BURST", "0"),
        ("DBMCP_SQL_TIMEOUT_SECONDS", "0"),
        ("DBMCP_SQL_TIMEOUT_SECONDS", "31"),  # longer than any analysis question needs
        ("DBMCP_SQL_MAX_CONCURRENCY", "0"),
        ("DBMCP_SQL_MAX_CONCURRENCY", "33"),
    ],
)
def test_out_of_range_setting_stops_start(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    """Invalid values fail at start-up, not at the first request."""
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValidationError):
        ServerSettings()


def test_allowed_hosts_must_be_a_json_list(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic_settings import SettingsError

    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("DBMCP_ALLOWED_HOSTS", "mcp.example.com")  # forgot the JSON brackets
    with pytest.raises(SettingsError):
        ServerSettings()


def test_app_does_not_start_with_a_broken_key_file(tmp_path: Path) -> None:
    """create_app loads the keys first, so a broken key file stops the start [BSI DEV.4.9]."""
    from database_mcp.app import create_app
    from database_mcp.auth import KeyConfigError

    keys = tmp_path / "keys.json"
    keys.write_text("{not json")
    settings = ServerSettings(
        db_app_password="x",  # type: ignore[arg-type]
        db_sql_password="y",  # type: ignore[arg-type]
        api_keys_file=keys,
    )
    with pytest.raises(KeyConfigError):
        create_app(settings, DatabaseSettings(host="localhost"))


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
