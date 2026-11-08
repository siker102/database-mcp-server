# KI-Nutzung

Der Code dieses Projekts wurde mit Claude Code (Modell Claude Opus 5.5) erzeugt. Steuerung über
`CLAUDE.md` und `docs/PLAN.md`; jede Etappe wird von mir reviewt. Pro Etappe: Ziel, was generiert
wurde, Fehler oder falsche Annahmen der KI und deren Korrektur.

## Vorbereitung – Plan, Annahmen, BSI-Abgleich

- **Ziel:** Plan und Regeln schärfen, bevor Code entsteht.
- **Generiert:** Überarbeitung von `CLAUDE.md` und `docs/PLAN.md`: Begründung für jede
  Entscheidung, BSI-Bezüge (Grundschutz++-Katalog aus der offiziellen BSI-Bibliothek ausgewertet),
  Abweichungen A1–A3, offene Punkte O1–O4, Kürzung auf vier Etappen.
- **Fehler / falsche Annahmen der KI:**
  - Der erste Plan nahm an, dass Rollen-Defaults (`default_transaction_read_only`,
    `statement_timeout`) eine Schutzschicht sind; sie lassen sich per `set_config` überschreiben.
    Korrigiert: Die Rechte sind die harte Grenze, zusätzlich `NullPool` und Funktions-Allowlist.
  - PLAN.md nannte eine RAM-Einstellung in Docker Desktop, die es mit dem WSL2-Backend nicht gibt.
  - Der ursprüngliche Plan (28–34 h) passte nicht in das Zeitbudget; gekürzt.

### Review und Anpassungen durch mich

## Etappe 1 – Grundgerüst und Datenbank

- **Ziel:** Projekt, Dev Container, CI-Gerüst, Datenbank mit Rollen, Rechten und Seed.
- **Generiert:** `pyproject.toml`/`uv.lock`, `config.py`, `db/` (Engines, Schema, Migration,
  Seed, `db-init`), `cli.py`, `.devcontainer/`, `Makefile`, `docker-compose.yml`, `Dockerfile`,
  CI-Workflow, ADR 0002 und 0007, Tests in `tests/integration/test_db_init.py` und
  `tests/unit/`.
- **Datensatz:** `data/titanic.csv` von
  `https://raw.githubusercontent.com/datasciencedojo/datasets/master/titanic.csv`
  (891 Zeilen, Spalten wie Kaggle `train.csv`, SHA-256
  `4a437fde05fe5264e1701a7387ac6fb75393772ba38bb2c9c566405af5af4bd7`); Lizenzlage siehe README.
- **Fehler / falsche Annahmen der KI:**
  - `Literal[0, 1]` in Pydantic wandelt CSV-Strings nicht um; ersetzt durch `int` mit Grenzen.
  - `format('%I', :name)` mit asyncpg braucht explizite Typen (`CAST(:name AS text)`).
  - Auf dem Windows-Host findet docker-py den Docker-Desktop-Pipe nur mit
    `DOCKER_HOST=npipe:////./pipe/dockerDesktopLinuxEngine` (im Dev Container nicht nötig).
  - Abweichung vom Plan: Migrationen liegen im Paket statt unter `migrations/`, damit sie im
    Image enthalten sind.
- **Lesereihenfolge:** `src/titanic_mcp/config.py` → `db/schema.py` →
  `db/migrations/versions/0001_passengers.py` → `db/init_db.py` → `db/seed.py` →
  `tests/integration/test_db_init.py`.

### Review und Anpassungen durch mich
