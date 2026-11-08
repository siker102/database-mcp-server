# ADR 0004 – Freies SQL mit Defense in Depth

Status: angenommen · Datum: 2026-11-08

## Kontext

Feste Tools decken nicht jede Analysefrage ab; LLMs formulieren SQL gut. Freies SQL ist aber die
größte Angriffsfläche des Servers – insbesondere, weil ein Agent per Prompt Injection manipuliert
sein kann.

## Entscheidung

Tool `run_sql` nur mit Scope `sql`, mit vier unabhängigen Schichten. **Die Datenbankrechte sind die
harte Grenze**, der Validator lehnt früh und verständlich ab.

1. **Validator** (`services/sql_guard.py`, sqlglot, Dialekt `postgres`): genau ein Statement, nur
   `SELECT`/Set-Operationen; kein DML/DDL/`COPY`/`SET`/`GRANT`/Transaktionsbefehl irgendwo im
   Baum (auch nicht in CTEs); kein `SELECT INTO`, kein `FOR UPDATE/SHARE`; Tabellen nur
   `passengers_public` (und CTE-Namen), keine Schema-Qualifizierung, keine Tabellenfunktionen;
   Funktionen nur aus einer Allowlist; höchstens 5000 Zeichen. **Ausgeführt wird der vom Parser
   neu erzeugte SQL-Text**, nicht der Originaltext: Was geprüft wurde, ist genau das, was läuft
   (kein Unterschied zwischen Parser und Datenbank, z. B. bei Kommentaren).
2. **Ausführung** (`services/sql_runner.py`): Rolle `titanic_sql`, eigene Engine mit `NullPool`,
   Read-only-Transaktion, `SET LOCAL statement_timeout`, App-Timeout als Rückfallebene,
   Semaphore für parallele Abfragen, `fetchmany(limit + 1)` auf einem serverseitigen Cursor →
   `truncated`.
3. **Datenbankrechte**: `titanic_sql` darf nur `SELECT` auf `passengers_public`;
   `temp_file_limit` begrenzt Plattenverbrauch [BSI KONF.9.1].
4. **Audit** jedes Aufrufs mit Query-Text, Ergebnis und Zeilenzahl, ohne Ergebnisdaten.

## Begründung

- Allowlist statt Blocklist: PostgreSQL hat Hunderte Funktionen; eine Blocklist wäre nie
  vollständig.
- Rollen-Defaults (`statement_timeout`, `default_transaction_read_only`) kann jede Sitzung per
  `set_config` überschreiben. `NullPool` sorgt dafür, dass eine solche Änderung nie die nächste
  Abfrage erreicht; der Test `test_null_pool_prevents_session_state_leaking` belegt das.
- Fehlermeldungen helfen dem Agent (unbekannte Spalte, Timeout, verbotene Funktion), verraten aber
  keine Interna [BSI DEV.3.3].

## Alternativen

- **Kein freies SQL**: sicherer, aber für Analysefragen deutlich schwächer.
- **Regex-Prüfung**: erkennt Verschachtelungen und Kommentare nicht zuverlässig.
- **Nur Datenbankrechte, kein Validator**: technisch ausreichend für Vertraulichkeit/Integrität,
  aber Agents bekämen schlechtere Fehlermeldungen, und teure Funktionen wie `pg_sleep` oder
  `generate_series` kämen bis zur Datenbank.

## Konsequenzen

- Lesender Zugriff auf Systemkataloge (`pg_catalog`) ist in PostgreSQL für jede Rolle erlaubt; hier
  blockt ihn nur der Validator. Die Kataloge enthalten keine Passagierdaten (bekannte Grenze in
  `docs/SECURITY.md`).
- Neue erlaubte Funktionen müssen bewusst in die Allowlist aufgenommen werden.
