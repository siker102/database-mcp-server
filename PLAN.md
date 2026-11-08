# Umsetzungsplan – Titanic MCP Server

Dieser Plan gehört nach `docs/PLAN.md`. Regeln, Zielarchitektur und technische Details stehen in
`CLAUDE.md` im Projekt-Root. Der Plan beschreibt die Reihenfolge, die Aufgaben pro Phase und wann
eine Phase fertig ist.

## Ausgangslage

**Aufgabe:** Ein in Python geschriebener MCP-Server, über den AI-Agents anderer Teams mit einer
Datenbank (Titanic-Datensatz) interagieren. Ziel ist ein produktionsreifer Stand.

**Antworten des Auftraggebers:**

- Zielplattform ist Kubernetes.
- Sporadische Nutzung, also zunächst geringe Last.
- Tech-Stack frei wählbar; KI-Nutzung erlaubt, muss dokumentiert werden.

**Eigene Annahmen** (kommen ins README unter „Annahmen“):

- Der Server läuft zentral; Agents verbinden sich über HTTP und authentifizieren sich pro Team.
- Agents dürfen nur lesen. Freies SQL nur für Teams mit Berechtigung `sql`.
- `name`, `ticket` und `cabin` werden wie personenbezogene Daten behandelt, obwohl der Datensatz
  öffentlich ist – der Server soll auch für echte Daten taugen.
- Es gibt keine Vorgaben für Logging, Monitoring oder CI; gewählt werden verbreitete Standards
  (JSON-Logs auf stdout, Prometheus, GitHub Actions).
- „Produktionsreif“ heißt hier: sicher, getestet, beobachtbar, per CI geprüft, auf Kubernetes
  deploybar und reproduzierbar lokal aufbaubar.
- Abgabe als Git-Repository mit Doku; Demo im Gespräch.

## Voraussetzungen auf dem Windows-PC

1. Docker Desktop mit WSL2-Backend; in den Einstellungen mindestens 6 GB RAM zuweisen.
2. VS Code mit der Erweiterung „Dev Containers“.
3. Git. In den Git-Einstellungen keine automatische Umwandlung in Windows-Zeilenenden erzwingen;
   die `.gitattributes` im Repo regelt das.
4. Für gute Performance das Repo entweder im WSL-Dateisystem ablegen oder in VS Code
   „Dev Containers: Clone Repository in Container Volume“ verwenden.

Alles Weitere (Python, uv, OpenTofu, kind, Helm, kubectl) kommt mit dem Dev Container.

## Phasen

Zeitangaben sind grobe Schätzungen. Nach jeder Phase: `make check`, Eintrag in
`docs/AI_USAGE.md`, Commit, Review durch mich.

### Phase 0 – Grundgerüst und Dev Container (2–3 h)

- `uv`-Projekt mit `src/`-Layout, Paket `titanic_mcp`, Python 3.12
- `.devcontainer/` laut `CLAUDE.md` (docker-in-docker, OpenTofu, kubectl, Helm, kind, uv; Ports
  8000 und 30080 weiterleiten)
- `.gitattributes`, `.gitignore` (inkl. `.env`, OpenTofu-State, `.terraform/`)
- ruff, mypy, pytest, pre-commit konfigurieren; `Makefile` mit den Basis-Targets
- `config.py` mit pydantic-settings, `.env.example`
- `docker-compose.yml` zunächst nur mit Postgres
- CI-Grundgerüst: Lint, Typecheck, Tests
- ADR 0007 (Dev Container)

**Fertig, wenn:** Der Dev Container unter Windows startet, `make check` grün ist und die CI läuft.

### Phase 1 – Datenbank (ca. 3 h)

- CSV in `data/titanic.csv` prüfen: Spalten, Typen, Zeilenzahl, fehlende Werte. Quelle und Lizenz
  im README festhalten. Weicht die CSV vom erwarteten Kaggle-Format ab: nachfragen.
- SQLAlchemy-Modell, Alembic-Migration für Tabelle `passengers` und View `passengers_public`
- CLI-Befehl `db-init`: Migrationen ausführen, Rollen und Rechte setzen, Daten idempotent laden.
  Das Anlegen der Rollen ist per Flag abschaltbar (`--create-roles`), weil in echten Umgebungen
  meist ein DB-Team die Rollen bereitstellt.
- Seed validiert jede Zeile mit Pydantic; ungültige Zeilen brechen mit klarer Meldung ab.
- Integrationstests (testcontainers): Zeilenzahl stimmt, erneutes `db-init` ändert nichts,
  `titanic_sql` kann `passengers` nicht lesen und nicht schreiben.
- ADR 0002 (PostgreSQL, Alembic, Seed-Job)

**Fertig, wenn:** `make up && make db-init` eine befüllte Datenbank mit korrekten Rechten liefert.

### Phase 2 – MCP-Server und feste Tools (4–5 h)

- Installierte SDK-Version prüfen und die echte API für Server, Tools, Streamable HTTP (stateless)
  und strukturierte Ausgaben nachlesen, bevor Code entsteht.
- Prüfen und dokumentieren, welche Protokollversionen der Server akzeptiert. Wichtig für die
  Demo: Funktionieren Clients, die noch eine ältere Spec-Version sprechen?
- `app.py`: ASGI-App mit MCP unter `/mcp`, `/healthz`, `/readyz`, Lifespan für die DB-Engines
- Services und Tools: `describe_dataset`, `search_passengers`, `get_passenger`, `get_statistics`
- PII-Maskierung im Service-Layer (vorerst über einen Platzhalter für die Scopes, echte Auth folgt
  in Phase 3)
- Unit-Tests für die Services; Integrationstest mit dem MCP-Client des SDK über HTTP
- ADR 0001 (SDK, Spec-Version, stateless Streamable HTTP), ADR 0005 (PII)

**Fertig, wenn:** Ein MCP-Client über HTTP alle vier Tools aufrufen kann und die Tests grün sind.

### Phase 3 – Authentifizierung, Scopes, Rate Limiting (ca. 3 h)

- Key-Prüfung laut `CLAUDE.md` (SDK-Token-Verifier, falls vorhanden, sonst Middleware)
- Scopes `read`, `pii`, `sql` durchsetzen; Tools ohne Scope nicht nutzbar
- Token-Bucket pro `key_id`
- `scripts/generate_api_key.py`
- Tests: ohne Key, falscher Key, falscher Scope, Rate Limit greift, kein Key taucht in Logs auf
- ADR 0003 (API-Keys statt OAuth, Migrationspfad zu OAuth 2.1)

**Fertig, wenn:** Jeder Tool-Aufruf ohne gültigen Key oder passenden Scope abgelehnt wird.

### Phase 4 – SQL-Tool und Härtung (4–5 h)

- `services/sql_guard.py` mit sqlglot: Validierung laut `CLAUDE.md`
- Ausführung mit Rolle `titanic_sql`, Read-only-Transaktion, Timeout, Zeilenlimit, `truncated`
- Tool `run_sql` mit hilfreichen Fehlermeldungen für den Agent
- `tests/security/`: Angriffs-Queries, mindestens
  - `DELETE`, `UPDATE`, `INSERT`, `DROP`, `TRUNCATE`, `COPY`, `SET`, `GRANT`
  - mehrere Statements, Kommentare zum Verstecken von Statements
  - datenverändernde CTEs, `SELECT INTO`, `FOR UPDATE`
  - Zugriff auf `passengers`, `pg_catalog`, `information_schema`
  - `pg_sleep` (Timeout greift), `pg_read_file`, `dblink`, `lo_*`, `set_config`
  - sehr große Ergebnismengen (Limit greift)
- Jede Schutzschicht zusätzlich einzeln testen (z. B. Rolle ohne Validator)
- ADR 0004 (freies SQL mit Defense in Depth), `docs/SECURITY.md` mit Bedrohungsmodell
  (u. a. SQL-Injection, Prompt Injection, Datenabfluss, Missbrauch durch Last)

**Fertig, wenn:** Alle Angriffs-Queries abgelehnt werden und gültige SELECTs korrekt funktionieren.

### Phase 5 – Observability (2–3 h)

- structlog-Konfiguration, `request_id` pro Request
- Audit-Event pro Tool-Aufruf (ohne Ergebnisdaten)
- Prometheus-Metriken auf Port 9090
- `/readyz` prüft die DB
- Tests: Audit-Event wird geschrieben und enthält keine Ergebnisdaten; Metriken zählen

**Fertig, wenn:** Ein Tool-Aufruf ein vollständiges Audit-Event und passende Metriken erzeugt.

### Phase 6 – Container und Helm-Chart (3–4 h)

- `Dockerfile`: Multi-Stage mit uv, Non-Root-User, kompatibel mit read-only Root-Filesystem
- `docker-compose.yml` vervollständigen: Postgres, `db-init`, Server
- Helm-Chart `deploy/helm/titanic-mcp`:
  - Deployment (2 Replicas), Service (8000 MCP, 9090 Metriken), Ingress optional (aus)
  - Probes, Ressourcen, `securityContext` (runAsNonRoot, readOnlyRootFilesystem, alle
    Capabilities entfernt, seccomp `RuntimeDefault`), `automountServiceAccountToken: false`
  - PodDisruptionBudget, NetworkPolicy (eingehend nur 8000/9090, ausgehend nur DB und DNS)
  - Migrations-/Seed-Job als Helm-Hook (pre-install, pre-upgrade)
  - Secrets nur über `existingSecret`-Referenzen, `values.schema.json` zur Validierung
  - optional ServiceMonitor für den Prometheus Operator
- CI: hadolint, Image-Build, Trivy (bricht bei HIGH/CRITICAL ab), `helm lint`,
  `helm template | kubeconform`, pip-audit

**Fertig, wenn:** `make up` den kompletten Stack startet und das Chart alle Prüfungen besteht.

### Phase 7 – IaC für die lokale Umgebung und E2E-Test (4–5 h)

- OpenTofu-Module `infra/modules/local-cluster` und `infra/modules/titanic-mcp`,
  Umgebung `infra/envs/local` (Details in `CLAUDE.md`)
- `make infra-up`, `make infra-down`, `make smoke`
- `scripts/smoke.py`: verbindet sich per MCP-Client mit `localhost:30080`, ruft
  `describe_dataset`, `get_statistics` und `run_sql` auf, prüft einen abgelehnten Aufruf ohne Key
- CI-Job „e2e“: derselbe Ablauf (`make infra-up && make smoke`) auf dem GitHub-Runner
- `tofu fmt -check`, `tofu validate` in `make check` und CI
- ADR 0006 (Helm + OpenTofu, In-Memory-Rate-Limit bei geringer Last)

**Fertig, wenn:** Ein frisch geklontes Repo im Dev Container mit `make infra-up && make smoke`
grün durchläuft – auf dem Windows-PC und in der CI.

### Phase 8 – Dokumentation und Demo (ca. 3 h)

- `README.md`: Überblick, Schnellstart (Windows + Codespaces), Architektur-Kurzfassung,
  Tools und Scopes, Konfiguration, Annahmen, Ausblick
- `docs/ARCHITECTURE.md` mit Diagramm, `docs/RUNBOOK.md` (Deployment, Key-Rotation, typische
  Fehler, wo man Logs und Metriken findet)
- Anleitung, wie man einen echten Agent anbindet (MCP Inspector; Claude Code bzw. Claude Desktop mit
  HTTP-Transport und `Authorization`-Header) – vorher mit den aktuellen Client-Versionen testen
- `docs/AI_USAGE.md` abschließen (meine Reflexion ergänze ich selbst)
- Abschnitt „Bewusst nicht umgesetzt / nächste Schritte“

**Fertig, wenn:** Jemand ohne Vorwissen mit dem README das Projekt starten und testen kann.

## Abgabe-Checkliste

- [ ] Frischer Clone → Dev Container → `make check` grün
- [ ] `make infra-up && make smoke` grün, `make infra-down` räumt vollständig auf
- [ ] CI auf `main` grün (inkl. E2E)
- [ ] Keine Secrets im Repo (auch nicht in der Git-Historie)
- [ ] Alle ADRs vorhanden, README-Annahmen aktuell
- [ ] `docs/AI_USAGE.md` mit eigener Reflexion ergänzt
- [ ] Demo einmal komplett durchgespielt

## Ausblick (bewusst nicht umgesetzt)

- OAuth 2.1 gemäß MCP-Autorisierungsprofil statt API-Keys
- Verteiltes Rate Limiting (z. B. Redis) bei mehr Last oder vielen Replicas
- Autoscaling (HPA), Tracing mit OpenTelemetry
- Postgres in Produktion als Managed Service oder über einen Operator (z. B. CloudNativePG)
- `infra/envs/prod` mit Remote-State und externer Datenbank
- Schreibende Tools mit Berechtigungen pro Operation
