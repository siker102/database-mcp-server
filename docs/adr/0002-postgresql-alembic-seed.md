# ADR 0002 – PostgreSQL, Alembic und Seed per Job

Status: angenommen · Datum: 2026-11-08

## Kontext

Agents anderer Teams lesen den Titanic-Datensatz, teils über freies SQL. Der Server soll so gebaut
sein, dass er auch für echte, sensible Daten taugt. Zielplattform ist Kubernetes.

## Entscheidung

- **PostgreSQL** (lokal 17, Image per Digest gepinnt) als externe Abhängigkeit.
- **Drei Rollen** nach Least Privilege [BSI BER.4.1, DEV.2.3, KONF.6.1]:
  - `titanic_owner` besitzt das Schema, nur für `db-init`.
  - `titanic_app` liest `passengers` und `passengers_public` (feste Tools).
  - `titanic_sql` liest **nur** die View `passengers_public` (freies SQL).
- **View ohne `security_invoker`**: Sie läuft mit den Rechten des Owners, deshalb braucht
  `titanic_sql` keinerlei Recht auf die Basistabelle.
- **Alembic** für das Schema; die Migrationen liegen im Paket (`src/titanic_mcp/db/migrations`),
  damit sie im Image enthalten sind. Ausgeführt werden sie nur über `titanic-mcp db-init`, das die
  Verbindung übergibt – keine Zugangsdaten in einer Konfigurationsdatei.
- **`db-init`** als eigener Schritt (Compose-Service, später Helm-Hook-Job): Migrationen, Rollen
  (optional, `--create-roles`), Rechte, idempotenter Seed. Jede Zeile wird vor dem Schreiben mit
  Pydantic validiert; eine ungültige Zeile bricht mit Zeilennummer ab [BSI DEV.2.6.1].
- **TLS zur Datenbank** standardmäßig mit `verify-full`; nur der Compose-Stack schaltet TLS
  ausdrücklich ab [BSI KONF.14.1, DEV.4.9]. asyncpg fällt mit einem SSLContext nicht auf
  Klartext zurück; ein Test belegt das.

## Begründung

- Rollen und `GRANT`s sind die harte Grenze für freies SQL; ein Fehler im SQL-Validator allein
  öffnet damit keinen Zugriff auf PII oder Schreibrechte.
- Der laufende Server hat keine Schreibrechte; selbst eine vollständige Übernahme des Prozesses
  kann die Daten nicht verändern.
- Idempotentes `db-init` darf bei jedem Deployment laufen.
- Eine Datensicherung ist nicht nötig: Die Daten sind statisch und aus `data/titanic.csv`
  reproduzierbar [BSI APP.4.4.A5].

## Alternativen

- **SQLite oder DuckDB im Image**: einfacher (kein Netzwerk, keine Zugangsdaten, Datei
  schreibgeschützt), kennen aber keine Rollen und `GRANT`s – genau die harte Grenze für freies SQL
  fehlt dann. Verworfen.
- **Schema per `create_all` beim Serverstart**: keine Versionierung, und der Server bräuchte
  DDL-Rechte. Verworfen.
- **Seed beim Serverstart**: der Server bräuchte Schreibrechte. Verworfen.

## Konsequenzen

- `ALTER ROLE ... SET temp_file_limit` erfordert Superuser-Rechte. Lokal ist `titanic_owner` der
  Superuser des Postgres-Containers; in echten Umgebungen setzt das DB-Team diese Einstellung
  (siehe RUNBOOK). Der Owner sollte dort kein Superuser sein.
- Rollen-Defaults (`default_transaction_read_only`, `statement_timeout`) kann jede Sitzung
  überschreiben; sie sind nur eine Bremse, die Rechte sind die Grenze (siehe ADR 0004).
