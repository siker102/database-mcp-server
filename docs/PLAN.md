# Umsetzungsplan – Database MCP Server

Regeln, Zielarchitektur und technische Details samt Begründungen stehen in `CLAUDE.md` im
Projekt-Root. Der Plan beschreibt die Reihenfolge, die Aufgaben pro Etappe und wann eine Etappe
fertig ist.

## Ausgangslage

**Aufgabe:** Ein in Python geschriebener MCP-Server, über den AI-Agents anderer Teams mit einer
Datenbank (Titanic-Datensatz) interagieren. Ziel ist ein produktionsreifer Stand.

**Antworten des Auftraggebers:**

- Zielplattform ist Kubernetes.
- Sporadische Nutzung, also zunächst geringe Last.
- Tech-Stack frei wählbar; KI-Nutzung erlaubt, muss dokumentiert werden.

**Zeitrahmen:** höchstens 8 Stunden für das gesamte Projekt. Was darüber hinausgeht oder von der
unbekannten Infrastruktur des Auftraggebers abhängt, wird als offener Punkt mit konkretem nächsten
Schritt dokumentiert statt umgesetzt.

**Eigene Annahmen** (kommen ins README unter „Annahmen“, jeweils mit Begründung):

- **Zentraler Server über HTTP, Authentifizierung pro Agent.** Grund: Agents mehrerer Teams greifen
  auf eine gemeinsame Datenbank zu; ein zentraler Dienst im Cluster hält Zugangsdaten, Regeln und
  Audit an einer Stelle. Ein lokaler stdio-Server pro Agent müsste jedem Team DB-Zugangsdaten geben.
- **Berechtigung pro Agent, nicht pro Nutzer.** Der Server kennt den Agent und sein Team hinter dem
  Key, nicht den Menschen, der dem Agent eine Frage stellt. Wer einen Agent betreibt, verantwortet,
  wer ihn nutzen darf. Grund: Es ist kein Identity Provider vorgegeben; Nutzeridentität per OAuth
  steht im Ausblick.
- **Agents dürfen nur lesen; freies SQL nur mit Berechtigung `sql`.** Grund: Die Aufgabe nennt
  keine Schreibzugriffe, der Datensatz ist statisch, und jeder Schreibzugriff würde die
  Angriffsfläche vergrößern, ohne einen Nutzen zu bringen.
- **`name`, `ticket` und `cabin` werden wie personenbezogene Daten behandelt**, obwohl der Datensatz
  öffentlich ist. Grund: Der Server soll auch für echte Personendaten taugen. Bewusste Grenze:
  Feld-Maskierung ist keine Anonymisierung (siehe `docs/SECURITY.md`).
- **TLS terminiert am Ingress bzw. Gateway.** Grund: Zertifikate verwaltet die Plattform; die App
  bleibt einfach und muss keine Zertifikate rotieren. Lokal läuft HTTP.
- **Schutz vor Lastangriffen ohne gültigen Key liegt am Ingress** (Limit pro IP). Grund: Die App
  begrenzt pro Key; einen Zustand pro IP zu halten ist Aufgabe der vorgelagerten Infrastruktur.
- **Für Logging, Monitoring und CI gibt es keine Vorgaben.** Der Server liefert deshalb
  Standardschnittstellen (JSON-Logs auf stdout, Prometheus-Endpunkt) statt einer festen Anbindung an
  ein Produkt. Grund: So fügt er sich in die bestehende Plattform des Teams ein – Kubernetes sammelt
  stdout ohnehin ein, und den Prometheus-Endpunkt lesen auch OpenTelemetry Collector und Datadog
  Agent. Die CI ruft ausschließlich Make-Targets auf und lässt sich so auf andere CI-Systeme
  übertragen.
- **„Produktionsreif“ heißt hier:** sicher, getestet, beobachtbar, per CI geprüft, auf Kubernetes
  deploybar und reproduzierbar lokal aufbaubar. Grund: Das sind die Kriterien, nach denen ein
  Plattform-Team einen Dienst in Betrieb nimmt; Funktionsumfang ist zweitrangig.
- **Sicherheitsmaßstab sind die Vorgaben des BSI** (Grundschutz++, Kompendium-Bausteine SYS.1.6 und
  APP.4.4, TR-02102, TR-03183-2) auf dem Sicherheitsniveau „normal“. Grund: anerkannter,
  öffentlicher Stand der Technik in Deutschland, gegen den sich jede Entscheidung prüfen lässt.
  Organisatorische Anforderungen (ISMS, Personal, Gebäude) liegen außerhalb des Projekts.
- **Abgabe als Git-Repository mit Doku; Demo im Gespräch.**

## Quellen für die BSI-Bezüge

Bezüge wie `[BSI DEV.3.4]` oder `[BSI SYS.1.6.A17]` lassen sich hier nachschlagen. Alle Links wurden
am 08.11.2026 geprüft.

| Kürzel im Text | Quelle | Link |
|---|---|---|
| `DEV.*`, `KONF.*`, `BER.*`, `DET.*`, `TEST.*`, `ASST.*` | Grundschutz++, Anwenderkatalog (BSI-Stand-der-Technik-Bibliothek, OSCAL-JSON). Für die Bezüge verwendeter Stand: Commit `c3f3785` vom 30.10.2026 | [Repository](https://github.com/BSI-Bund/Stand-der-Technik-Bibliothek) · [Katalog in genau dieser Version](https://github.com/BSI-Bund/Stand-der-Technik-Bibliothek/blob/c3f37859fdf302394de39e89252eb3e72a45953c/control_layer/Grundschutz%2B%2B/Grundschutz%2B%2B-resolved_catalog.json) |
| (wie oben, lesbar) | Stand-der-Technik-Viewer: zeigt den Katalog im Browser; dort nach der ID suchen, z. B. „DEV.3.4“. Community-Projekt, vom BSI-Repository verlinkt | [Viewer](https://bsi-community.github.io/Stand-der-Technik-Viewer/) |
| `SYS.1.6.A*`, `APP.4.4.A*` | IT-Grundschutz-Kompendium, Bausteine SYS.1.6 Containerisierung und APP.4.4 Kubernetes (offizielle Seite mit PDF-Download) | [BSI: IT-Grundschutz-Kompendium](https://www.bsi.bund.de/DE/Themen/Unternehmen-und-Organisationen/Standards-und-Zertifizierung/IT-Grundschutz/IT-Grundschutz-Kompendium/it-grundschutz-kompendium_node.html) |
| (wie oben, Übersicht) | Anforderungsliste der Bausteine, aufbereitet von ComplianceAsCode auf Basis der Edition 2022. Nummern vor der Verwendung im Chart gegen die offizielle Edition prüfen | [APP.4.4](https://complianceascode.github.io/content-pages/rendered-policies/ocp4/bsi_app_4_4.html) · [SYS.1.6](https://complianceascode.github.io/content-pages/rendered-policies/ocp4/bsi_sys_1_6.html) |
| `Entwurf Mindeststandard TLS` | Entwurf des Mindeststandards TLS (gleiches Repository) | [Katalog](https://github.com/BSI-Bund/Stand-der-Technik-Bibliothek/blob/c3f37859fdf302394de39e89252eb3e72a45953c/control_layer/Mindeststandard-TLS/Entwurf-Mindeststandard-TLS-catalog.json) |
| `TR-02102` | Technische Richtlinie „Kryptographische Verfahren: Empfehlungen und Schlüssellängen“ (Teil 1: Verfahren, Teil 2: TLS) | [BSI: TR-02102](https://www.bsi.bund.de/dok/TR-02102) |
| `TR-03183-2` | Technische Richtlinie „Cyber Resilience Requirements“, Teil 2: SBOM | [BSI: TR-03183](https://www.bsi.bund.de/dok/TR-03183) |
| `TR-03185` | Technische Richtlinie „Sicherer Software-Lebenszyklus“ (von DEV.1.1 als Verfahren referenziert) | [BSI: TR-03185](https://www.bsi.bund.de/dok/TR-03185) |
| Python-Versionen | Offizieller Release-Kalender von Python; Python 3.14: PEP 745 | [Release-Kalender (JSON)](https://peps.python.org/api/release-cycle.json) · [PEP 745](https://peps.python.org/pep-0745/) |

## Voraussetzungen auf dem Windows-PC

1. Docker Desktop mit WSL2-Backend. Für Postgres, Server und Testcontainer reicht der
   Standardspeicher von WSL2 (50 % des Arbeitsspeichers); eine eigene Einstellung ist nicht nötig.
2. VS Code mit der Erweiterung „Dev Containers“.
3. Git. In den Git-Einstellungen keine automatische Umwandlung in Windows-Zeilenenden erzwingen;
   die `.gitattributes` im Repo regelt das.
4. Für gute Performance das Repo entweder im WSL-Dateisystem ablegen oder in VS Code
   „Dev Containers: Clone Repository in Container Volume“ verwenden.

Alles Weitere (Python, uv, Helm, kubeconform) kommt mit dem Dev Container.

## Etappen

**Zeitbudget:** 8 Stunden *eigene* Arbeitszeit für Review, Verstehen und Testen. Den Code schreibt
die KI; jede Zeile muss ich im Interview erklären können. Die Zeitangaben beziehen sich auf meine
Arbeitszeit, nicht auf die Laufzeit der KI.

**Gegenüber dem ursprünglichen Plan gekürzt** (ursprünglich 9 Phasen, geschätzt 28–34 h):

- **Lokaler Kubernetes-Cluster per OpenTofu und kind gestrichen** (ursprünglich Phase 7, 4–5 h).
  Grund: größter Zeit- und Risikoblock (kind in docker-in-docker unter Windows). Das Helm-Chart wird
  trotzdem geprüft (`helm lint`, `kubeconform`, `trivy config`); der Ende-zu-Ende-Test läuft gegen
  den Compose-Stack. Das Konzept steht als offener Punkt O4 im Plan.
- **Observability direkt in Etappe 2** statt als eigene Phase. Grund: Logging und Audit
  nachträglich einzubauen wäre doppelte Arbeit.
- **Dokumentation laufend** in der Etappe, in der die Entscheidung fällt (ADRs, `SECURITY.md`,
  README). Am Ende bleibt nur Feinschliff.
- **Vier Etappen statt neun Phasen**, also vier Review-Stopps.

Nach jeder Etappe: `make check`, Eintrag in `docs/AI_USAGE.md`, Commit, Review durch mich. Reicht
die Zeit nicht, wird in Etappe 4 in dieser Reihenfolge gekürzt: ServiceMonitor, PodDisruptionBudget,
Sicherheits-Header – alles mit Eintrag im Ausblick.

### Etappe 1 – Grundgerüst und Datenbank (ca. 1,5 h)

- `uv`-Projekt mit `src/`-Layout, Paket `database_mcp`, Python 3.14 (Begründung in `CLAUDE.md`)
- `.devcontainer/` laut `CLAUDE.md` (docker-in-docker, Helm, kubeconform, uv; Port 8000
  weiterleiten)
- `.gitattributes` (bereits vorab angelegt), `.gitignore` (inkl. `.env`)
- ruff, mypy, pytest, pre-commit konfigurieren; `Makefile` mit den Basis-Targets
- `config.py` mit pydantic-settings, `.env.example`
- CI-Grundgerüst: Lint, Typecheck, Tests
- `docker-compose.yml` mit Postgres
- CSV in `data/titanic.csv` prüfen: Spalten, Typen, Zeilenzahl, fehlende Werte. Quelle und Lizenz
  im README festhalten. Weicht die CSV vom erwarteten Kaggle-Format ab: nachfragen.
- SQLAlchemy-Modell, Alembic-Migration für Tabelle `passengers` und View `passengers_public`
- CLI-Befehl `db-init`: Migrationen ausführen, Rollen und Rechte setzen, Daten idempotent laden.
  Das Anlegen der Rollen ist per Flag abschaltbar (`--create-roles`), weil in echten Umgebungen
  meist ein DB-Team die Rollen bereitstellt.
- Seed validiert jede Zeile mit Pydantic; ungültige Zeilen brechen mit klarer Meldung ab.
- Rollen laut `CLAUDE.md`, inkl. `temp_file_limit` für `titanic_sql`
- DB-Verbindung mit `sslmode=verify-full` als Default; Compose schaltet TLS ausdrücklich ab
  (siehe „Entscheidungen aus dem BSI-Abgleich“)
- Integrationstests (testcontainers): Zeilenzahl stimmt, erneutes `db-init` ändert nichts,
  `titanic_sql` kann `passengers` nicht lesen und nicht schreiben, `passengers_public` aber lesen;
  mit `verify-full` scheitert die Verbindung zu einer Datenbank ohne TLS (kein stilles Herabstufen)
- ADR 0007 (Dev Container), ADR 0002 (PostgreSQL, Alembic, Seed-Job), mit verworfenen
  Alternativen: SQLite bzw. DuckDB im Image wären einfacher (kein Netzwerk, keine Zugangsdaten),
  kennen aber keine Rollen und `GRANT`s – und genau die sind die harte Grenze für freies SQL.
- README-Gerüst: Schnellstart, Voraussetzungen, Annahmen

**Fertig, wenn:** Der Dev Container unter Windows startet, `make check` grün ist, die CI läuft und
`make up && make db-init` eine befüllte Datenbank mit korrekten Rechten liefert.

### Etappe 2 – MCP-Server, feste Tools, Auth und Observability (ca. 2,5 h)

- Installierte SDK-Version prüfen und die echte API für Server, Tools, Streamable HTTP (stateless),
  strukturierte Ausgaben und Token-Verifier nachlesen, bevor Code entsteht.
- Prüfen und dokumentieren, welche Protokollversionen der Server akzeptiert. Wichtig für die
  Demo: Funktionieren Clients, die noch eine ältere Spec-Version sprechen?
- Prüfen, was die Spec-Revision zur Prüfung von `Origin`/`Host` (Schutz vor DNS-Rebinding)
  verlangt und wie das SDK das umsetzt
- `app.py`: ASGI-App mit MCP unter `/mcp`, `/healthz`, `/readyz` (prüft die DB), Lifespan für die
  DB-Engines
- Services und Tools: `describe_dataset`, `search_passengers`, `get_passenger`, `get_statistics`
- PII-Maskierung im Service-Layer; ohne `pii` liest das Repository aus `passengers_public`
- Key-Prüfung laut `CLAUDE.md` (SDK-Token-Verifier, falls vorhanden, sonst Middleware); Scopes
  `read`, `pii`, `sql` durchsetzen; Tools ohne Scope nicht nutzbar
- Ein Key pro Agent, mehrere aktive Keys pro Agent (für Rotation ohne Ausfall); Ablaufdatum
  `expires_at` pro Key, höchstens 90 Tage mit `pii`, sonst 365 Tage (beim Start geprüft);
  Hash mit Salt; `scripts/generate_api_key.py`
- Token-Bucket pro `key_id`
- structlog-Konfiguration, `request_id` pro Request
- Audit-Event pro Tool-Aufruf (ohne Ergebnisdaten, PII-Parameter maskiert)
- Sicherheitsrelevante Ereignisse mit Zeitpunkt, Quelle (inkl. Client-IP aus dem Header des
  Ingress) und Zielobjekt, inkl. erfolgreicher und fehlgeschlagener Authentifizierung
  [BSI DET.3.1, DET.3.1.1, DET.3.1.7]
- Prometheus-Metriken auf Port 9090
- Tests:
  - Unit-Tests für die Services; Integrationstest mit dem MCP-Client des SDK über HTTP
  - ohne `pii` wird keine PII-Spalte abgefragt
  - ohne Key, falscher Key, abgelaufener Key, falscher Scope, Rate Limit greift, kein Key taucht
    in Logs auf, zwei Keys desselben Agents funktionieren parallel, Key-Konfiguration mit zu
    langer `pii`-Laufzeit verhindert den Start
  - Audit-Event wird geschrieben und enthält keine Ergebnisdaten; `name_contains` erscheint
    maskiert; Authentifizierungs-Events werden geschrieben; Metriken zählen
- ADR 0001 (SDK, Spec-Version, stateless Streamable HTTP, Origin-Prüfung), ADR 0003 (API-Keys
  statt OAuth, Migrationspfad zu OAuth 2.1, Identität pro Agent, Abweichungen A2 und A3),
  ADR 0005 (PII)

**Fertig, wenn:** Ein MCP-Client über HTTP alle vier Tools aufrufen kann, jeder Aufruf ohne gültigen
Key oder passenden Scope abgelehnt wird und jeder Aufruf ein vollständiges Audit-Event und passende
Metriken erzeugt.

### Etappe 3 – SQL-Tool und Härtung (ca. 2 h)

- `services/sql_guard.py` mit sqlglot: Validierung laut `CLAUDE.md`
- Ausführung mit Rolle `titanic_sql`, Read-only-Transaktion, Timeout, Zeilenlimit, `truncated`;
  eigene Engine mit `NullPool`, App-Timeout (`asyncio.timeout`), begrenzte Parallelität,
  maximale Query-Länge
- Tool `run_sql` mit hilfreichen Fehlermeldungen für den Agent
- `tests/security/`: Angriffs-Queries, mindestens
  - `DELETE`, `UPDATE`, `INSERT`, `DROP`, `TRUNCATE`, `COPY`, `SET`, `GRANT`
  - mehrere Statements, Kommentare zum Verstecken von Statements
  - datenverändernde CTEs, `SELECT INTO`, `FOR UPDATE`
  - Zugriff auf `passengers`, `pg_catalog`, `information_schema`
  - `pg_sleep` (Timeout greift), `pg_read_file`, `dblink`, `lo_*`, `set_config`
  - Ohne Validator: `set_config('statement_timeout', '0', false)` wirkt nicht auf die nächste
    Abfrage (belegt, dass `NullPool` Sitzungszustand verhindert)
  - teure Abfragen, z. B. mehrfacher Cross-Join (Timeout bzw. `temp_file_limit` greift)
  - sehr große Ergebnismengen (Limit greift)
- Jede Schutzschicht zusätzlich einzeln testen (z. B. Rolle ohne Validator)
- ADR 0004 (freies SQL mit Defense in Depth; die Datenbankrechte sind die harte Grenze, der
  Validator lehnt früh und verständlich ab)
- `docs/SECURITY.md` mit Bedrohungsmodell (u. a. SQL-Injection, Prompt Injection, Datenabfluss,
  Missbrauch durch Last), bekannten Grenzen (Identität nur pro Agent, Feld-Maskierung ist keine
  Anonymisierung, Revisionssicherheit der Audit-Logs liegt bei der Log-Plattform), einer
  Zuordnungstabelle BSI-Anforderung → Maßnahme → Stelle im Repo und den Abweichungen vom BSI mit
  Begründung und kompensierender Maßnahme

**Fertig, wenn:** Alle Angriffs-Queries abgelehnt werden und gültige SELECTs korrekt funktionieren.

### Etappe 4 – Container, Helm-Chart, Ende-zu-Ende-Test und Abschluss (ca. 2 h)

- `Dockerfile`: Multi-Stage mit uv, Non-Root-User, kompatibel mit read-only Root-Filesystem;
  Basis-Image aus offizieller Quelle, per Digest gepinnt [BSI SYS.1.6.A6, SYS.1.6.A17]
- `docker-compose.yml` vervollständigen: Postgres, `db-init`, Server
- `scripts/smoke.py` und `make smoke`: verbindet sich per MCP-Client mit dem Compose-Stack
  (`localhost:8000`), ruft `describe_dataset`, `get_statistics` und `run_sql` auf, prüft einen
  abgelehnten Aufruf ohne Key
- Helm-Chart `deploy/helm/database-mcp`:
  - Deployment (2 Replicas, damit ein Pod-Neustart oder Node-Wechsel keinen Ausfall verursacht),
    Service (8000 MCP, 9090 Metriken), Ingress optional (aus; im README dokumentiert: TLS 1.3 bzw.
    TLS 1.2 mit PFS und Limit pro IP gehören dorthin)
  - Eigener Namespace für die Anwendung [BSI APP.4.4.A1]
  - Probes [BSI APP.4.4.A11], Requests und Limits für CPU, Speicher und ephemeren Speicher
    [BSI SYS.1.6.A15], `securityContext` (runAsNonRoot, readOnlyRootFilesystem, alle Capabilities
    entfernt, seccomp `RuntimeDefault`) [BSI SYS.1.6.A17, SYS.1.6.A21, SYS.1.6.A23]
  - Eigener ServiceAccount ohne Rechte, `automountServiceAccountToken: false` [BSI APP.4.4.A9]
  - PodDisruptionBudget; NetworkPolicy mit Default-Deny für den Namespace, dann eingehend nur
    8000/9090, ausgehend nur DB und DNS [BSI APP.4.4.A7, APP.4.4.A18]
  - Migrations-/Seed-Job als Helm-Hook (pre-install, pre-upgrade); ein Dienst pro Container
    [BSI SYS.1.6.A11]
  - Secrets nur über `existingSecret`-Referenzen, `values.schema.json` zur Validierung; im README
    als Anforderung an den Betrieb: Verschlüsselung der Secrets im Cluster (etcd-Verschlüsselung
    oder External Secrets) [BSI KONF.1.5]
  - Sicherheits-Header in den HTTP-Antworten der App (`X-Content-Type-Options: nosniff`,
    `Cache-Control: no-store`) [BSI KONF.12.1.10]
  - optional ServiceMonitor für den Prometheus Operator
- CI: hadolint, Image-Build, Trivy (Schwachstellen, bricht bei HIGH/CRITICAL ab [BSI DET.5.3];
  Konfigurationsprüfung von Dockerfile und Chart [BSI APP.4.4.A13]; SBOM im CycloneDX-Format als
  CI-Artefakt [BSI DEV.4.3, TR-03183-2]), `helm lint`, `helm template | kubeconform`, pip-audit
  [BSI DEV.4.5]; Job „e2e“: Compose-Stack starten und `make smoke`
- ADR 0006 (Helm-Chart, Compose statt lokalem Cluster, In-Memory-Rate-Limit bei geringer Last)
- Abschluss der Doku:
  - `README.md`: Überblick, Schnellstart (Windows + Codespaces), Architektur-Kurzfassung, Tools
    und Scopes, Konfiguration, Annahmen, Ausblick
  - `docs/ARCHITECTURE.md` mit Diagramm [BSI DEV.2.2]
  - `docs/RUNBOOK.md`: Deployment, Key-Rotation mit zwei parallelen Keys, Widerruf und Sperrung
    kompromittierter Keys [BSI BER.5.3, BER.5.14], Vergabe von `pii` nur auf begründeten Antrag
    und regelmäßige Überprüfung der vergebenen Scopes [BSI BER.4.3, BER.4.4], Anforderungen an die
    Plattform aus Abweichung A1, typische Fehler, wo man Logs und Metriken findet
  - Anleitung, wie man einen echten Agent anbindet (MCP Inspector; Claude Code bzw. Claude Desktop
    mit HTTP-Transport und `Authorization`-Header) – vorher mit den aktuellen Client-Versionen
    testen
  - `docs/AI_USAGE.md` abschließen (meine Reflexion ergänze ich selbst)

**Fertig, wenn:** `make up && make smoke` grün ist, das Chart alle Prüfungen besteht und jemand ohne
Vorwissen mit dem README das Projekt starten und testen kann.

## Entscheidungen aus dem BSI-Abgleich

Stellen, an denen der ursprüngliche Plan vom BSI abwich oder das BSI mehr verlangt. Quellen siehe
„Quellen für die BSI-Bezüge“. Diese Inhalte wandern in Etappe 3 nach `docs/SECURITY.md`.

### Umgesetzt (Plan an das BSI angepasst)

- **Datenbankverbindung mit TLS** [BSI KONF.14.1, KONF.14.1.1]: Die App verbindet sich standardmäßig
  mit `sslmode=verify-full`, prüft also Verschlüsselung *und* Zertifikat der Datenbank. Unsicherere
  Modi sind nur ausdrücklich konfigurierbar (Security by Default [BSI DEV.4.9]). Docker Compose
  schaltet TLS ausdrücklich ab (nur Entwicklung, nur `localhost`). Ein Integrationstest belegt,
  dass `verify-full` die Verbindung zu einer Datenbank ohne TLS ablehnt, statt still auf eine
  unverschlüsselte Verbindung herabzustufen. Ein Test mit echtem Zertifikat folgt erst mit einem
  lokalen Cluster (offener Punkt O4).
- **Salt für den Key-Hash** [BSI DEV.3.4]: Pro Key wird ein zufälliges Salt (16 Byte) gespeichert,
  geprüft wird `SHA-256(salt ‖ secret)`. Fachlich nötig wäre es bei 256 Bit Zufall nicht, es kostet
  aber nur wenige Zeilen und erfüllt die Anforderung wörtlich.
- **Ein Key pro Agent** [BSI BER.3.16]: Jeder Key gehört zu genau einem technischen Konsumenten
  (`agent`) eines Teams (`team`). Zugriffe bleiben damit einem Konsumenten zuordenbar, und ein
  einzelner Key lässt sich sperren, ohne andere Agents des Teams zu stören. Scopes, Rate Limit und
  Metriken bleiben unverändert; Metriken laufen weiter mit dem Label `team`.

### Begründete Abweichungen

Jeweils: was das BSI verlangt, was wir tun, warum, welches Risiko bleibt und wann neu zu bewerten ist.

#### A1 – Kein TLS zwischen Ingress und Pod in der App [BSI KONF.14.1, KONF.14.1.1 – SOLLTE]

- **Anforderung:** Kommunikation über Netze soll verschlüsselt sein, ausdrücklich auch zwischen
  Servern; unverschlüsselte Verbindungen sollen deaktiviert werden.
- **Was wir tun:** TLS terminiert am Ingress (TLS 1.3 bzw. TLS 1.2 mit PFS). Zwischen Ingress und
  Pod spricht die App selbst HTTP. Verschlüsselung auf dieser Strecke ist eine dokumentierte
  Anforderung an die Plattform: ein Service Mesh mit mTLS (z. B. Istio, Linkerd) oder ein Ingress,
  der per TLS zum Pod weiterleitet.
- **Warum:** Zertifikate in der App müssten ausgestellt, verteilt, rotiert und überwacht werden
  [BSI DEV.7.2]. Ein Service Mesh erledigt das für alle Dienste des Clusters einheitlich und
  automatisch; eine eigene Lösung pro Dienst wäre fehleranfälliger und schwerer zu betreiben. In
  Kubernetes ist das die übliche Aufgabenteilung zwischen Anwendung und Plattform.
- **Risiko:** Wer den Netzverkehr im Cluster mitlesen kann (kompromittierter Node, falsch
  konfiguriertes CNI), könnte Bearer-Keys und Antworten sehen.
- **Kompensation:** NetworkPolicy mit Default-Deny; nur der Ingress darf Port 8000 erreichen
  [BSI APP.4.4.A7, APP.4.4.A18]. Keys laufen ab und sind einzeln sperrbar. Die Datenbankstrecke ist
  unabhängig davon per TLS geschützt.
- **Restrisiko:** gering, solange die Plattform mTLS bereitstellt; ohne Mesh mittel.
- **Neu bewerten:** wenn die Zielplattform kein Mesh hat. Dann TLS in uvicorn aktivieren
  (Zertifikat per cert-manager in den Pod gemountet).

#### A2 – Kein Replay-Schutz für Bearer-Keys [BSI DEV.3.1 – SOLLTE]

- **Anforderung:** Eine Anwendung, die Anfragen anderer Systeme annimmt, soll Replay-Angriffe
  blockieren, also die Wiederverwendung einer abgefangenen Anfrage, etwa durch Einmal-Werte
  (Nonces, Sequenznummern) oder signierte Anfragen.
- **Was wir tun:** Der Key ist ein statisches Bearer-Token. Wer ihn kennt, kann beliebig viele
  Anfragen stellen; einen Mechanismus gegen das Wiederholen einzelner Anfragen gibt es nicht.
- **Warum:** MCP-Clients (Claude, MCP Inspector, die SDKs) unterstützen nur einen
  `Authorization`-Header, keine signierten Anfragen und keine Nonces. Eine eigene Signatur würde
  jeden Standard-Client ausschließen – der Server wäre für die anderen Teams nicht nutzbar. Zudem
  zielt die Anforderung auf Anfragen, deren Wiederholung Schaden anrichtet (z. B. eine zweite
  Überweisung). Alle Tools sind lesend und ohne Seiteneffekte; eine wiederholte Anfrage liefert nur
  dieselben Daten noch einmal, die der Angreifer schon mit der ersten Anfrage bekommen hätte.
- **Risiko:** Das eigentliche Risiko ist nicht das Wiederholen einer Anfrage, sondern ein
  gestohlener Key: Wer ihn hat, liest mit den Rechten des Agents, bis der Key abläuft oder gesperrt
  wird.
- **Kompensation:** TLS verhindert das Abfangen auf dem Transportweg [BSI KONF.14.1]; Keys haben
  ein Ablaufdatum [BSI BER.7.10] und sind einzeln widerrufbar [BSI BER.5.3]; jeder Aufruf wird mit
  `key_id`, Agent und Client-IP protokolliert [BSI DET.3.1.7], ungewöhnliche Nutzung ist in den
  Metriken sichtbar [BSI DET.4.6]; das Rate Limit begrenzt den Schaden pro Zeit.
- **Restrisiko:** gering, wegen ausschließlich lesender Tools und begrenzter Laufzeit der Keys.
- **Neu bewerten:** sobald schreibende Tools dazukommen, oder beim Umstieg auf OAuth 2.1 mit
  kurzlebigen Access-Tokens (Minuten statt Monate), das die MCP-Spezifikation vorsieht.

#### A3 – Keine Mehr-Faktor-Authentisierung für den `pii`-Scope [BSI BER.5.9 – SOLLTE]

- **Anforderung:** Zugänge mit weitreichenden Berechtigungen – ausdrücklich auch der Zugriff auf
  besonders sensible Daten – sollen mit mehreren Faktoren gesichert sein.
- **Was wir tun:** Ein Key mit `pii` ist ein einzelner Faktor (Wissen bzw. Besitz des Keys).
- **Warum:** MFA setzt einen Menschen voraus, der einen zweiten Faktor bestätigt (App, Token,
  Biometrie). Hier authentifizieren sich Programme ohne Bedienung; ein zweiter Faktor lässt sich
  dort nicht sinnvoll abfragen. Das Pendant für Maschinen wäre ein zweites, unabhängiges Merkmal,
  etwa ein Client-Zertifikat (mTLS) zusätzlich zum Key. Das setzt eine PKI für alle Teams voraus,
  die nicht vorgegeben ist.
- **Risiko:** Ein gestohlener `pii`-Key gibt Zugriff auf Namen, Ticket- und Kabinennummern.
- **Kompensation:**
  - `pii` wird nur auf begründeten Antrag vergeben und regelmäßig auf Erforderlichkeit geprüft
    [BSI BER.4.3, BER.4.4] (Ablauf im Runbook).
  - Kürzere Laufzeit: Keys mit `pii` dürfen höchstens 90 Tage gültig sein, andere höchstens
    365 Tage. Der Server erzwingt das beim Laden der Key-Konfiguration und startet sonst nicht.
  - `run_sql` erreicht nie PII-Spalten, unabhängig vom Scope.
  - Jeder Zugriff auf PII-Felder steht im Audit-Log.
- **Restrisiko:** mittel bei echten Personendaten, gering beim öffentlichen Titanic-Datensatz.
- **Neu bewerten:** bei echten Personendaten oder sobald die Plattform Workload-Identitäten bzw.
  mTLS zwischen Teams anbietet.

### Offene Punkte (bewusst nicht umgesetzt)

Diese Anforderungen gehören zum Betrieb in der Zielumgebung. Wie sie umgesetzt werden, hängt von
der vorhandenen Infrastruktur des Auftraggebers ab (Secret-Management, Registry, Update-Prozess),
die nicht bekannt ist. Zusätzlich sprengen sie den Zeitrahmen von 8 Stunden (siehe
„Ausgangslage“). Ohne Kenntnis der Plattform wäre jede Lösung geraten und müsste später
wieder ersetzt werden. Für jeden Punkt steht fest, was zu tun wäre und welche Infos dafür fehlen.

- **O1 – Automatische Rotation von Dienste-Zugangsdaten** [BSI BER.3.21 – SOLLTE]
  - *Heute:* DB-Passwörter werden pro Installation zufällig erzeugt, aber nicht automatisch
    gewechselt. API-Keys haben ein erzwungenes Ablaufdatum, die Erneuerung läuft manuell über das
    Runbook.
  - *Nächster Schritt:* DB-Zugangsdaten über das Secret-Management der Plattform, idealerweise
    dynamisch und kurzlebig (z. B. Vault/OpenBao mit Database Secrets Engine, angebunden über den
    External Secrets Operator). Warnung als Log-Event und Metrik, wenn ein API-Key bald abläuft.
  - *Fehlende Infos:* Welches Secret-Management nutzt die Plattform? Wie werden Keys an andere
    Teams übergeben?
- **O2 – Signierte Images** [BSI SYS.1.6.A12 – Standard]
  - *Heute:* Images werden in der CI gebaut und gescannt (Trivy), aber nicht signiert. Basis-Images
    sind per Digest gepinnt.
  - *Nächster Schritt:* Image in die Registry des Auftraggebers pushen, in der CI mit cosign
    signieren (schlüssellos über GitHub OIDC oder mit einem Schlüssel aus dem KMS der Plattform),
    SBOM als signierte Attestation anhängen, im Cluster per Admission Controller (z. B. Kyverno)
    nur signierte Images zulassen.
  - *Fehlende Infos:* Welche Registry? Gibt es eine Signatur- bzw. Admission-Policy? Darf ein
    öffentliches Transparenz-Log (Rekor) genutzt werden?
- **O3 – Automatische Abhängigkeits-Updates** [BSI DET.5.10, KONF.8.2, DEV.4.5 – SOLLTE]
  - *Heute:* Alle Versionen sind gepinnt (reproduzierbar [BSI DEV.4.4]); pip-audit und Trivy
    finden bekannte Schwachstellen bei jedem CI-Lauf, aktualisieren aber nichts und laufen nur bei
    Commits.
  - *Nächster Schritt:* Dependabot oder Renovate für Python-Pakete, GitHub Actions, Basis-Image
    und Dev Container; Update-PRs laufen durch die volle CI und werden manuell freigegeben
    [BSI TEST.4.1]. Zusätzlich ein wöchentlicher geplanter Scan, damit neue Schwachstellen auch
    ohne Commit auffallen [BSI DET.5.10.2].
  - *Fehlende Infos:* Welches Update-Werkzeug ist im Unternehmen etabliert? Welche Fristen gelten
    für Sicherheitsupdates?
- **O4 – Produktionsnahe Staging-Umgebung per Infrastructure as Code** [BSI TEST.4.1.2 – SOLLTE]
  - *Heute:* Das Helm-Chart wird statisch geprüft (`helm lint`, `kubeconform`, `trivy config`);
    der Ende-zu-Ende-Test läuft gegen den Compose-Stack. Ob Chart, Probes, NetworkPolicy und
    `securityContext` in einem echten Cluster zusammen funktionieren, wird nicht automatisch
    getestet. Die DB-Verbindung mit `verify-full` wird nur negativ getestet (kein Herabstufen).
  - *Nächster Schritt:* OpenTofu erzeugt mit einem Befehl einen lokalen kind-Cluster mit
    Image-Registry, Postgres mit TLS (CA über den Provider `hashicorp/tls`), zufällig erzeugten
    Secrets und dem Helm-Release; derselbe Ablauf läuft in der CI als E2E-Job. Das
    Deployment-Modul wäre ohne Änderung für echte Cluster (Remote-State, externe Datenbank)
    nutzbar. Aufwand ca. 4–5 h, größtes Risiko: kind in docker-in-docker unter Windows.
  - *Fehlende Infos:* Gibt es beim Auftraggeber bereits eine Staging-Umgebung oder ein
    IaC-Werkzeug (Terraform/OpenTofu, Pulumi, GitOps mit Argo CD bzw. Flux)?

## Abgabe-Checkliste

- [x] Frischer Clone → Dev Container → `make check` grün (mit der Dev-Containers-CLI von VS Code
  geprüft; unter Windows ggf. Wayland-Einstellung aus dem README nötig)
- [x] `make up && make smoke` grün, `make down` räumt vollständig auf
- [x] CI auf `main` grün (inkl. E2E)
- [x] Keine Secrets im Repo (auch nicht in der Git-Historie; Historie am 09.11.2026 durchsucht)
- [x] Alle ADRs vorhanden, README-Annahmen aktuell
- [ ] `docs/AI_USAGE.md` mit eigener Reflexion ergänzt
- [ ] Demo einmal komplett durchgespielt

## Ausblick (bewusst nicht umgesetzt)

Jeweils mit dem Grund, warum es jetzt nicht nötig ist. Die BSI-bezogenen offenen Punkte O1–O4
stehen oben unter „Entscheidungen aus dem BSI-Abgleich“.

- OAuth 2.1 gemäß MCP-Autorisierungsprofil statt API-Keys, inkl. Nutzeridentität hinter dem Agent –
  braucht einen Identity Provider, der nicht vorgegeben ist.
- Verteiltes Rate Limiting (z. B. Redis) – lohnt sich erst bei mehr Last oder vielen Replicas.
- Autoscaling (HPA), Tracing mit OpenTelemetry – bei sporadischer Nutzung reichen zwei Replicas,
  Logs und Metriken.
- Postgres in Produktion als Managed Service oder über einen Operator (z. B. CloudNativePG) – hängt
  von der Plattform des Teams ab.
- Deployment per OpenTofu in echte Umgebungen mit Remote-State und externer Datenbank – braucht
  eine echte Zielumgebung (siehe O4).
- Manipulationssicheres Audit-Log (eigene Senke mit Aufbewahrung) – Sache der Log-Plattform; der
  Server markiert Audit-Events bereits eindeutig.
- Schutz vor Re-Identifizierung über Quasi-Identifikatoren (z. B. Mindestgruppengrößen bei
  Aggregaten) – für den öffentlichen Titanic-Datensatz unverhältnismäßig, bei echten Personendaten
  nötig.
- Schreibende Tools mit Berechtigungen pro Operation – von der Aufgabe nicht gefordert.
- Secret-Scanning mit gitleaks (Pre-commit und CI) – aus Zeitgründen gestrichen; Ersatz: Hook
  `detect-private-key`, das Key-Präfix `dbmcp_` für Scanner der Plattform und gitignorierte
  `.env`/`secrets/`.
- Regelmäßiger Neustart der Pods (höchstens 24 h Laufzeit) [BSI APP.4.4.A21, erhöhter Schutzbedarf] –
  bei normalem Schutzbedarf nicht gefordert.
- Erweiterte Mandatory-Access-Control-Profile (AppArmor/SELinux) über seccomp `RuntimeDefault`
  hinaus [BSI SYS.1.6.A21, erhöhter Schutzbedarf] – hängt von der Plattform ab.
