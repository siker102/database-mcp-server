# Database MCP Server

Ein MCP-Server (Model Context Protocol), über den AI-Agents anderer Teams **lesend** auf den
Titanic-Datensatz zugreifen: vier feste Tools und – für berechtigte Agents – freies `SELECT` auf
einer View ohne personenbezogene Daten. Ziel ist ein produktionsreifer Stand: sicher, getestet,
beobachtbar, per CI geprüft, auf Kubernetes deploybar und lokal mit einem Befehl startbar.

Sicherheitsmaßstab sind die Vorgaben des BSI (Grundschutz++, Bausteine SYS.1.6 und APP.4.4,
TR-02102, TR-03183-2). Das Projekt *orientiert sich* daran; es ist kein Konformitätsnachweis.
Details: [`docs/SECURITY.md`](docs/SECURITY.md).

**Inhalt:** [Schnellstart](#schnellstart-lokal) ·
[Checkliste für Agents](#checkliste-was-ein-agent-für-die-verbindung-braucht) ·
[Manuell testen](#manuell-testen) ·
[Tests ausführen](#tests-ausführen) · [Testübersicht](#testübersicht) ·
[Ports und Firewall](#netzwerk-ports-und-firewall-regeln) ·
[Kubernetes](#integration-in-einen-kubernetes-cluster) · [Tools und Scopes](#tools-und-scopes) ·
[Konfiguration](#konfiguration) · [Architektur](#architektur) · [Annahmen](#annahmen) ·
[Grenzen und offene Punkte](#bekannte-grenzen-offene-punkte-und-ausblick) ·
[Datensatz](#datensatz-quelle-und-lizenz) · [KI-Nutzung](#ki-nutzung)

## Schnellstart (lokal)

**Voraussetzungen (Windows):** Docker Desktop mit WSL2-Backend, VS Code mit der Erweiterung
„Dev Containers“, Git. Alternativ GitHub Codespaces ohne lokale Installation. Für gute Performance
das Repo im WSL-Dateisystem ablegen oder „Dev Containers: Clone Repository in Container Volume“
nutzen. Alles Weitere (Python 3.14, uv, Helm, kubeconform) bringt der Dev Container mit.

1. Repo in VS Code öffnen → „Reopen in Container“. Der Container installiert die Abhängigkeiten
   (`make install`) und die Pre-commit-Hooks.
2. Im Terminal des Containers:

```bash
make up      # erzeugt .env + Demo-Key (falls nicht vorhanden), startet Postgres, db-init, Server
make smoke   # Ende-zu-Ende-Test gegen http://localhost:8000/mcp
make down    # Stack stoppen und Volumes löschen
```

`make up` erzeugt beim ersten Aufruf zufällige DB-Passwörter in `.env` und einen Demo-Key mit allen
Scopes (90 Tage) in `secrets/` – beides gitignored, es gibt keine festen Default-Zugangsdaten. Der
Demo-Key wird einmal angezeigt und liegt in `secrets/demo_api_key`. Port 8000 wird vom Dev
Container nach Windows weitergeleitet.

`make help` listet alle Befehle.

## Checkliste: Was ein Agent für die Verbindung braucht

Ein Agent kann den Server nutzen, wenn **alle** Punkte erfüllt sind – egal ob der Server lokal, als
Container im Netz oder im Kubernetes-Cluster läuft. Die rechte Spalte zeigt, woran man einen
fehlenden Punkt erkennt.

| # | Voraussetzung | Wo einstellen | Fehlerbild, wenn es fehlt |
|---|---|---|---|
| 1 | **Netzwerk:** Die URL `http(s)://<host>[:<port>]/mcp` ist vom Agent aus erreichbar | Compose: `DBMCP_BIND_ADDRESS` (Default `127.0.0.1`, also nur dieser Rechner). Kubernetes: über den Ingress (Pfad `/mcp`); direkter Zugriff auf den Service ist durch die NetworkPolicy gesperrt | Timeout oder „connection refused“ |
| 2 | **Hostname freigegeben:** Der Name (oder die IP), den der Agent in der URL verwendet, steht in der Host-Allowlist | `DBMCP_ALLOWED_HOSTS` bzw. Helm `server.allowedHosts`; der Ingress-Host wird von Helm automatisch ergänzt | HTTP **421** „Invalid Host header“ |
| 3 | **Nur für Browser-Clients:** Die Herkunft (`Origin`) ist freigegeben | `DBMCP_ALLOWED_ORIGINS` bzw. `server.allowedOrigins` (Default leer, denn Agents sind keine Browser) | HTTP **403** „Invalid Origin header“ |
| 4 | **Gültiger API-Key:** Der Agent sendet `Authorization: Bearer dbmcp_…`; der Key steht in der Key-Datei und ist nicht abgelaufen | Key mit `scripts/generate_api_key.py` erzeugen, Eintrag in `api_keys.json` (Compose: `secrets/`, Kubernetes: Secret `database-mcp-api-keys`), Server neu starten | HTTP **401** mit `WWW-Authenticate: Bearer` (bewusst ohne Begründung) |
| 5 | **Passender Scope:** `read` für die festen Tools, `pii` zusätzlich für Namen/Ticket/Kabine, `sql` für `run_sql` | Beim Erzeugen des Keys (`--scopes read,sql`) | Das Tool fehlt in `tools/list`; ein direkter Aufruf liefert einen Tool-Fehler |
| 6 | **Passender Client:** spricht MCP über Streamable HTTP (Revision 2026-07-28 oder 2024-11-05 bis 2025-11-25) und kann einen eigenen `Authorization`-Header setzen | Konfiguration des Agents bzw. MCP-Clients | Verbindungsfehler im Client; HTTP 400 bei falschem `Content-Type` |
| 7 | **Rate Limit eingehalten:** höchstens 60 Aufrufe pro Minute und Key (Burst 20), pro Pod | `DBMCP_RATE_LIMIT_PER_MINUTE`, `DBMCP_RATE_LIMIT_BURST` | HTTP **429** |
| 8 | **Verschlüsselt außerhalb des eigenen Rechners:** über Netze nur per HTTPS, sonst wandert der Key im Klartext | TLS am Ingress (TLS 1.3 oder 1.2 mit PFS) | Kein Fehler – deshalb besonders wichtig |

### Host-Allowlist (`DBMCP_ALLOWED_HOSTS`)

Jede HTTP-Anfrage enthält den Header `Host` mit dem Namen aus der URL, z. B. `Host: mcp.example.com`.
Der Server beantwortet `/mcp` nur, wenn dieser Wert in der Allowlist steht. Das ist der Schutz des
MCP-SDK gegen **DNS-Rebinding**: Eine bösartige Webseite könnte sonst einen eigenen Domainnamen auf
die interne Adresse des Servers umbiegen und den Browser eines Mitarbeiters Anfragen an den Server
stellen lassen. Mit der Allowlist antwortet der Server nur auf Namen, unter denen er wirklich
erreichbar sein soll.

Format: JSON-Liste. Ein Eintrag passt entweder exakt (`"mcp.example.com"`, `"10.0.0.5:8000"`) oder
mit beliebigem Port (`"mcp.example.com:*"`). Achtung: `:*` passt nur, wenn die URL einen Port
enthält. Bei HTTPS auf Port 443 sendet der Client den Namen **ohne** Port; dafür braucht es den
exakten Eintrag ohne Port (so setzt Helm den Ingress-Host).

| Zugriff über | Eintrag in der Allowlist |
|---|---|
| `http://localhost:8000/mcp` (Default) | `"localhost:*"` |
| `http://192.168.1.50:8000/mcp` (Container im Netz) | `"192.168.1.50:*"` |
| `https://mcp.example.com/mcp` (Ingress) | `"mcp.example.com"` – setzt Helm automatisch |
| `http://database-mcp.database-mcp.svc.cluster.local:8000/mcp` (direkt im Cluster) | `"database-mcp.database-mcp.svc.cluster.local:*"` |

Beispiel Compose im vertrauenswürdigen Testnetz (in `.env`, danach `make up`):

```bash
DBMCP_BIND_ADDRESS=0.0.0.0
DBMCP_ALLOWED_HOSTS=["localhost:*","127.0.0.1:*","192.168.1.50:*"]
```

Dann ist der Server ohne TLS im Netz erreichbar – nur für Tests. Gegebenenfalls muss die Firewall
des Rechners eingehende Verbindungen auf Port 8000 erlauben.

### Weg einer Anfrage im Cluster

```
Agent ──HTTPS 443──▶ Load Balancer / Ingress-Controller ──HTTP 8000──▶ Service database-mcp ──▶ Pod
                     · terminiert TLS                       (nur aus dem Namespace des
                     · Rate Limit pro Client-IP               Ingress-Controllers erlaubt)
                     · reicht nur den Pfad /mcp weiter
                     · setzt X-Forwarded-For, behält den Host-Header
```

Agents – auch solche im selben Cluster – rufen den Server über die Ingress-URL auf (z. B.
`https://mcp.example.com/mcp`). Grund: Nur dort gibt es TLS, das Rate Limit pro IP und die
Client-IP für das Audit-Log; außerdem ist es für alle Agents derselbe Weg. Ein direkter Zugriff auf
den Service aus einem anderen Namespace ist möglich, braucht aber drei Änderungen: die NetworkPolicy
um den Namespace des Agents erweitern, den Service-Namen in `server.allowedHosts` aufnehmen und die
Strecke per Service Mesh verschlüsseln (Abweichung A1).

## Manuell testen

Voraussetzung: `make up` läuft. Den Demo-Key holen:

```bash
export KEY=$(cat secrets/demo_api_key)
```

### Mit dem Smoke-Skript

```bash
uv run python scripts/smoke.py --url http://localhost:8000/mcp
```

Prüft `tools/list`, `describe_dataset`, `get_statistics`, `run_sql`, einen Aufruf ohne Key (401) und
ein verbotenes `DELETE`. Ausgabe je Prüfung `OK`/`FAIL`, Exit-Code 0 nur bei Erfolg.

### Mit MCP Inspector (CLI)

Laut [Inspector-README](https://github.com/modelcontextprotocol/inspector/blob/main/clients/cli/README.md)
(Version 2.14.0; hier nicht ausgeführt, benötigt Node ≥ 22.23.4 bzw. ≥ 24.22.1):

```bash
npx @modelcontextprotocol/inspector --cli http://localhost:8000/mcp --method tools/list --header "Authorization: Bearer $KEY"
```

```bash
npx @modelcontextprotocol/inspector --cli http://localhost:8000/mcp --header "Authorization: Bearer $KEY" --method tools/call --tool-name get_statistics --tool-args-json '{"group_by": ["pclass", "sex"]}'
```

Ohne `--cli` startet die Web-Oberfläche; dort Transport „Streamable HTTP“, URL
`http://localhost:8000/mcp` und den Header `Authorization: Bearer <key>` eintragen.

### Mit Claude Code

Laut [Claude-Code-Doku](https://code.claude.com/docs/en/mcp) (hier nicht ausgeführt):

```bash
claude mcp add --transport http titanic http://localhost:8000/mcp --header "Authorization: Bearer $KEY"
```

Danach in Claude Code z. B. fragen: „Wie hoch war die Überlebensrate nach Klasse und Geschlecht?“

**Claude Desktop:** Remote-MCP-Server werden dort als Connector über die Einstellungen
hinzugefügt. Ob dabei ein eigener `Authorization`-Header gesetzt werden kann, habe ich nicht
verifiziert; für Tests mit API-Key daher Claude Code oder den Inspector verwenden.

### Beispielaufrufe und erwartete Ergebnisse

| Aufruf | Erwartung |
|---|---|
| `describe_dataset` | Spalten mit Typ, Bedeutung, Wertebereich; `name`, `ticket`, `cabin` als `personal_data` markiert |
| `get_statistics {"group_by": ["pclass"]}` | Überlebensrate Klasse 1/2/3 ≈ 0,63 / 0,47 / 0,24 |
| `search_passengers {"sex": "female", "pclass": 1, "limit": 5}` | `total: 94`, 5 Datensätze |
| `get_passenger {"passenger_id": 1}` | mit `pii`: inkl. `name: "Braund, Mr. Owen Harris"`; ohne `pii`: ohne Namensfelder |
| `run_sql {"query": "SELECT sex, count(*) FROM passengers_public GROUP BY sex"}` | `female 314`, `male 577` |

**Abgelehnte Aufrufe:**

```bash
# ohne Key -> HTTP 401 mit WWW-Authenticate: Bearer
curl -i -X POST http://localhost:8000/mcp -H "Content-Type: application/json" -d '{}'
```

- `run_sql {"query": "DELETE FROM passengers_public"}` → Tool-Fehler „Only SELECT queries are allowed.“
- `run_sql {"query": "SELECT name FROM passengers"}` → „Table 'passengers' is not allowed …“
- `run_sql {"query": "SELECT pg_sleep(10)"}` → „Function 'pg_sleep' is not allowed.“
- Falscher Scope: einen Key nur mit `read` erzeugen (siehe unten) – `run_sql` fehlt dann in
  `tools/list`, und ein direkter Aufruf ergibt „Access denied: tool 'run_sql' needs the 'sql' scope.“

Weitere Keys für Tests:

```bash
uv run python scripts/generate_api_key.py --agent test-reader --team local --scopes read --days 30 --append secrets/api_keys.json
docker compose restart server
```

Die Server-Logs (JSON, inkl. Audit-Events) zeigt `docker compose logs -f server`.

## Tests ausführen

Alle Tests laufen im Dev Container; Integrations- und Sicherheitstests starten PostgreSQL per
[testcontainers](https://testcontainers.com/) und brauchen daher Docker (im Dev Container über
docker-in-docker vorhanden).

```bash
make check   # ruff, Format-Check, mypy --strict, alle Tests mit Coverage >= 85 %, helm lint, kubeconform
```

Einzelne Teile:

```bash
make lint
make typecheck
make test
make helm-check
uv run pytest tests/unit             # schnell, ohne Docker
uv run pytest tests/integration      # braucht Docker
uv run pytest tests/security         # Validator ohne Docker, DB-Schichten mit Docker
uv run pytest -m "not integration" --no-cov   # nur Tests ohne Datenbank
```

Bei einzelnen Suites `--no-cov` anhängen, sonst greift die Coverage-Schwelle für das ganze Paket.

**Auf einem Windows-Host ohne Dev Container** (z. B. mit installiertem `uv`) braucht testcontainers
den Docker-Desktop-Pipe: `$env:DOCKER_HOST = "npipe:////./pipe/dockerDesktopLinuxEngine"`
(PowerShell), dann `uv run pytest`.

Die CI (`.github/workflows/ci.yml`) ruft dieselben Make-Targets auf und führt zusätzlich hadolint,
Trivy (Image und Konfiguration), pip-audit, die SBOM-Erzeugung und den Compose-E2E-Test aus.

## Testübersicht

Stand: 127 Tests, Coverage ca. 92 %.

| Datei | Was wird geprüft | Belegt |
|---|---|---|
| `tests/unit/test_config_and_seed.py` | Pflicht-Secrets ohne Default, Secrets nicht in `repr`, `verify-full` als Default, `prefer` abgelehnt, CLI bricht bei ungültiger Konfiguration ab; CSV: 891 Zeilen, falscher Header, ungültige Zeile mit Zeilennummer | Sichere Defaults [BSI DEV.4.8, DEV.4.9], Eingabevalidierung [DEV.2.6.1] |
| `tests/unit/test_auth.py` | Key-Erzeugung und gesalzener Hash, unterschiedliche Salts, zu lange Laufzeit verhindert Start, doppelte `key_id`, ungültige Key-Datei, falscher/unbekannter/abgelaufener Key, Token-Bucket, 429-Middleware | [BSI DEV.3.4, BER.7.10, BER.7.13, KONF.15.3] |
| `tests/integration/test_db_init.py` | 891 Zeilen geladen, zweites `db-init` ändert nichts, `titanic_sql` liest nur die View, beide Server-Rollen können nicht schreiben, `verify-full` stuft nicht auf Klartext herab | Least Privilege [BSI BER.4.1, DEV.2.3], Transportverschlüsselung [KONF.14.1] |
| `tests/integration/test_mcp_http.py` | Ganzer Server über HTTP mit dem SDK-Client: `tools/list` nach Scope gefiltert, moderne (2026-07-28) und ältere Protokollversion, PII nur mit `pii`, Filter/Pagination, Eingabefehler als Tool-Fehler, Scope-Prüfung auch bei direktem Aufruf, 401 ohne/mit falschem Key ohne Details, Health-Routen ohne Key, `/readyz` 503 ohne DB, falscher `Host` abgelehnt, 429, Audit-Event ohne Ergebnisdaten mit maskierter PII, Metriken | [BSI DEV.3.3, BER.4.6, DET.3.1, DET.3.1.1, DET.3.3, APP.4.4.A11], DNS-Rebinding-Schutz |
| `tests/security/test_sql_attacks.py` | 40 Angriffs-Queries gegen den Validator (DML/DDL, `COPY`, `SET`, mehrere Statements, Kommentare, datenverändernde CTEs, `SELECT INTO`, `FOR UPDATE`, Systemkataloge, `pg_sleep`, `pg_read_file`, `dblink`, `lo_*`, `set_config`, Tabellenfunktionen, Überlänge); gültige Analyse-Queries; Zeilenlimit/`truncated`; Cross-Join abgebrochen; Rolle `titanic_sql` blockt Angriffe **ohne** Validator; `NullPool` verhindert, dass `set_config` die nächste Abfrage beeinflusst; Timeout ohne Validator | Defense in Depth [BSI DEV.2.6, TEST.3.1], Ressourcen [KONF.9.1, KONF.15.3] |
| `scripts/smoke.py` (`make smoke`) | Ende-zu-Ende gegen den laufenden Compose-Stack bzw. einen Cluster | Bereitstellung funktioniert [BSI TEST.5.4] |

## Netzwerk: Ports und Firewall-Regeln

Der Server nimmt Verbindungen auf zwei Ports an und baut selbst nur Verbindungen zur Datenbank und
zum DNS auf; er ruft keine externen Dienste auf (keine Telemetrie, keine Downloads zur Laufzeit).
Alles, was nicht unten steht, wird nicht gebraucht und sollte gesperrt bleiben [BSI KONF.11.7,
APP.4.4.A7].

**Ports des Servers**

| Port | Protokoll | Inhalt | Wer darf zugreifen | Von außen erreichbar? |
|---|---|---|---|---|
| 8000 | TCP, HTTP | `/mcp` (MCP-Endpunkt), `/healthz`, `/readyz` | Ingress-Controller; kubelet für die Probes | Nur `/mcp`, und nur über den Ingress per HTTPS (der Ingress veröffentlicht ausschließlich den Pfad `/mcp`) |
| 9090 | TCP, HTTP | `/metrics` (Prometheus) | Prometheus im Monitoring-Namespace | Nein – Metriken verraten Teamnamen und Nutzungsmuster |

**Benötigte Verbindungen (Freigaben)**

| # | Quelle → Ziel | Port | Zweck | Wo freigeben |
|---|---|---|---|---|
| 1 | Agents der Teams → Ingress/Load Balancer | 443/TCP (HTTPS) | MCP-Aufrufe | Firewall/Security Group vor dem Ingress; Quellnetze der Teams per Allowlist einschränken. Port 80 nur für eine Weiterleitung auf HTTPS oder geschlossen |
| 2 | Ingress-Controller → Server-Pods | 8000/TCP | Weiterleitung von `/mcp` | NetworkPolicy des Charts (`networkPolicy.ingressNamespace`) |
| 3 | kubelet (Node) → Server-Pods | 8000/TCP | Liveness-, Readiness-, Startup-Probe | Laut Kubernetes-Dokumentation ist Verkehr vom eigenen Node zum Pod trotz NetworkPolicy erlaubt; manche CNIs verlangen eine eigene Regel – nach der Installation mit `kubectl get pods` prüfen, ob die Pods `Ready` werden |
| 4 | Prometheus → Server-Pods | 9090/TCP | Metriken abholen | NetworkPolicy des Charts (`networkPolicy.monitoringNamespace`) |
| 5 | Server-Pods und Job `db-init` → PostgreSQL | 5432/TCP (`database.port`) | Datenbank, immer TLS mit `verify-full` | NetworkPolicy (`networkPolicy.databaseCidr` auf den Adressbereich der DB eingrenzen); zusätzlich Firewall bzw. Security Group der Datenbank für die ausgehenden Adressen des Clusters (je nach CNI Pod-CIDR, Node-IPs oder NAT-Gateway) |
| 6 | Server-Pods und Job `db-init` → Cluster-DNS (`kube-dns`) | 53/UDP und 53/TCP | Namensauflösung, z. B. des DB-Hosts | NetworkPolicy des Charts |
| 7 | Nodes → Container-Registry | 443/TCP | Image ziehen | Firewall der Nodes (betrifft die Nodes, nicht die Pods) |

Alle anderen ein- und ausgehenden Verbindungen der Pods sperrt die Default-Deny-NetworkPolicy des
Charts. Voraussetzung ist ein CNI, das NetworkPolicies durchsetzt (z. B. Calico, Cilium); ohne ein
solches CNI sind die Regeln wirkungslos.

**PostgreSQL-Seite.** Die Datenbank muss Verbindungen aus dem Cluster nur per TLS annehmen. Beispiel
für `pg_hba.conf` (Adressbereich an den Cluster anpassen):

```text
# TYPE   DATABASE  USER                                   ADDRESS        METHOD
hostssl  titanic   titanic_owner,titanic_app,titanic_sql  10.0.0.0/16    scram-sha-256
```

`hostssl` lehnt unverschlüsselte Verbindungen auf Serverseite ab; zusammen mit `verify-full` in der
App ist die Strecke von beiden Seiten abgesichert [BSI KONF.14.1, KONF.14.1.1]. Die Rolle
`titanic_owner` braucht der Server selbst nicht; sie lässt sich auf die Zeit von Installation und
Upgrade (Job `db-init`) beschränken.

**Service Mesh.** Wird die Strecke Ingress → Pod über ein Service Mesh verschlüsselt (Abweichung A1
in `docs/PLAN.md`), braucht der Sidecar eigene Ports (bei Istio z. B. 15001, 15006, 15008, 15021,
15090). Die NetworkPolicy des Charts muss dann um diese Ports ergänzt oder durch die Policies des
Mesh ersetzt werden.

**Lokal (Compose).** Server (`8000`) und Postgres (`5432`) sind nur an `127.0.0.1` gebunden und
damit von anderen Rechnern im Netz nicht erreichbar; eine Firewall-Freigabe ist nicht nötig. Der Dev
Container leitet Port 8000 nach Windows weiter.

## Integration in einen Kubernetes-Cluster

Die folgenden Schritte installieren den Server in einen bestehenden Cluster mit externer
PostgreSQL-Datenbank. Geprüft ist das Chart statisch (`helm lint --strict`, `kubeconform`,
`trivy config`); eine Installation in einem echten Cluster ist nicht automatisiert getestet
(offener Punkt O4).

### 1. Voraussetzungen

- Kubernetes-Cluster (Manifeste gegen 1.33 validiert) mit einem CNI, das **NetworkPolicies**
  durchsetzt; `kubectl` und Helm 3.
- **PostgreSQL 16+** mit TLS und einem Zertifikat, das auf den Hostnamen ausgestellt ist; die CA
  als Datei (`db-ca.crt`), falls sie nicht in den System-CAs des Images liegt.
- **Ingress-Controller oder Gateway** mit TLS 1.3 oder TLS 1.2 mit Perfect Forward Secrecy
  (Entwurf Mindeststandard TLS des BSI, TR-02102-2), einem Zertifikat (z. B. über cert-manager),
  einem Rate Limit pro Client-IP und gesetztem `X-Forwarded-For`.
- **Anforderungen an die Plattform** (aus Abweichung A1 und [BSI KONF.1.5]):
  - Verschlüsselung zwischen Ingress und Pod, z. B. Service Mesh mit mTLS.
  - Verschlüsselung der Kubernetes-Secrets im Cluster (etcd-Verschlüsselung at rest) oder ein
    externes Secret-Management (External Secrets Operator, Vault).
  - Log-Plattform, die Audit-Events (`event=audit`) revisionssicher ablegt [BSI DET.3.5].
- Eine Container-Registry, aus der der Cluster ziehen darf.
- Die Netzwerk-Freigaben aus [Ports und Firewall-Regeln](#netzwerk-ports-und-firewall-regeln).

### 2. Image bauen und pushen

```bash
docker build -t registry.example.com/database-mcp:0.1.0 .
docker push registry.example.com/database-mcp:0.1.0
docker buildx imagetools inspect registry.example.com/database-mcp:0.1.0   # Digest für image.digest
```

### 3. Datenbankrollen anlegen

**Variante A – das DB-Team legt die Rollen an** (empfohlen; der Owner braucht dann kein
`CREATEROLE`). Als Superuser:

```sql
CREATE ROLE titanic_owner LOGIN PASSWORD '<owner-pw>';
CREATE DATABASE titanic OWNER titanic_owner;
CREATE ROLE titanic_app LOGIN PASSWORD '<app-pw>';
CREATE ROLE titanic_sql LOGIN PASSWORD '<sql-pw>';
ALTER ROLE titanic_app SET default_transaction_read_only = on;
ALTER ROLE titanic_sql SET default_transaction_read_only = on;
ALTER ROLE titanic_sql SET statement_timeout = '5s';
ALTER ROLE titanic_sql SET temp_file_limit = '64MB';
```

Der Job `db-init` legt danach Tabelle und View an, vergibt die Rechte (`GRANT`) und lädt die Daten.

**Variante B – `db-init` legt die Rollen an:** `dbInit.createRoles: true`. Dafür braucht
`titanic_owner` das Recht `CREATEROLE`; `temp_file_limit` kann nur ein Superuser setzen (der Job
warnt sonst und läuft weiter).

### 4. Namespace und Secrets anlegen

```bash
kubectl create namespace database-mcp
```

DB-Passwörter (Variante A: nur die Passwörter der Rollen; `owner-password` braucht nur der Job).
Die Werte werden interaktiv gelesen, damit sie nicht in der Shell-Historie landen:

```bash
read -rs OWNER_PW && read -rs APP_PW && read -rs SQL_PW
kubectl -n database-mcp create secret generic database-mcp-db \
  --from-literal=owner-password="$OWNER_PW" \
  --from-literal=app-password="$APP_PW" \
  --from-literal=sql-password="$SQL_PW"
unset OWNER_PW APP_PW SQL_PW
```

API-Keys: pro Agent einen Key erzeugen (der Key erscheint einmal auf stderr, in die Datei kommt nur
der gesalzene Hash):

```bash
uv run python scripts/generate_api_key.py --agent report-bot --team analytics --scopes read --days 365 --append api_keys.json
uv run python scripts/generate_api_key.py --agent sql-bot --team data-science --scopes read,sql --days 365 --append api_keys.json
kubectl -n database-mcp create secret generic database-mcp-api-keys --from-file=api_keys.json
shred -u api_keys.json
```

Zugangsdaten für eine private Registry (falls nötig; in `my-values.yaml` dann
`imagePullSecrets: [{name: registry-credentials}]` setzen):

```bash
read -rs REGISTRY_TOKEN
kubectl -n database-mcp create secret docker-registry registry-credentials \
  --docker-server=registry.example.com --docker-username=<user> --docker-password="$REGISTRY_TOKEN"
unset REGISTRY_TOKEN
```

CA der Datenbank (falls nötig):

```bash
kubectl -n database-mcp create secret generic database-mcp-db-ca --from-file=ca.crt=./db-ca.crt
```

### 5. Values-Datei

`my-values.yaml` (alle Optionen mit Erklärung: `deploy/helm/database-mcp/values.yaml`, Prüfung
durch `values.schema.json`):

```yaml
image:
  repository: registry.example.com/database-mcp
  tag: "0.1.0"
  digest: "sha256:<digest aus Schritt 2>"
database:
  host: postgres.db.svc.cluster.local
  port: 5432
  name: titanic
  sslmode: verify-full
  caSecret: database-mcp-db-ca
dbInit:
  createRoles: false
ingress:
  enabled: true
  className: <ingress-class>
  host: database-mcp.example.com
  tlsSecretName: database-mcp-tls
networkPolicy:
  ingressNamespace: <namespace des Ingress-Controllers>
  monitoringNamespace: monitoring
  databaseCidr: 10.20.30.0/24   # Adressbereich der Datenbank
serviceMonitor:
  enabled: true   # nur mit Prometheus Operator
```

### 6. Installieren

```bash
helm upgrade --install database-mcp deploy/helm/database-mcp -n database-mcp -f my-values.yaml --wait
```

Vor dem Deployment läuft der Job `database-mcp-db-init` (Helm-Hook pre-install/pre-upgrade).

### 7. Prüfen

```bash
kubectl -n database-mcp rollout status deployment/database-mcp
kubectl -n database-mcp get pods,networkpolicy,pdb
kubectl -n database-mcp logs deploy/database-mcp | head
```

Health-Routen (nicht über den Ingress veröffentlicht) per Port-Forward:

```bash
kubectl -n database-mcp port-forward svc/database-mcp 8000:8000
curl -s http://localhost:8000/healthz   # {"status":"ok"}
curl -s http://localhost:8000/readyz    # {"status":"ok"} wenn die DB erreichbar ist
```

Smoke-Test über den Ingress mit einem Key, der alle Scopes hat (z. B. eigens für den Test erzeugt
und danach widerrufen):

```bash
uv run python scripts/smoke.py --url https://database-mcp.example.com/mcp --key-file ./smoke-key.txt
```

Key-Rotation, Widerruf, regelmäßige Überprüfung der Scopes, Logs, Metriken und typische Fehler:
[`docs/RUNBOOK.md`](docs/RUNBOOK.md).

## Tools und Scopes

| Tool | Scope | Zweck |
|---|---|---|
| `describe_dataset` | `read` | Spalten, Typen, Bedeutungen, Wertebereiche |
| `search_passengers` | `read` | Filter (Überleben, Klasse, Geschlecht, Hafen, Alter, Fare), Pagination (max. 100); `name_contains` nur mit `pii` |
| `get_passenger` | `read` | Ein Datensatz per ID; Namensfelder nur mit `pii` |
| `get_statistics` | `read` | Anzahl, Überlebensrate, Ø Alter, Ø Fare, gruppiert nach `pclass`, `sex`, `embarked`, `survived` |
| `run_sql` | `sql` | Ein lesendes `SELECT` auf `passengers_public`, max. 500 Zeilen, Funktions-Allowlist |

Scopes: `read` (feste Tools ohne PII), `pii` (zusätzlich `name`, `ticket`, `cabin` und Namenssuche),
`sql` (`run_sql`, nie mit PII). Ein Agent sieht in `tools/list` nur seine Tools; jedes Tool prüft
den Scope zusätzlich selbst. Ohne Authentifizierung erreichbar sind nur `/healthz`, `/readyz` und
`/metrics` (Port 9090, nicht im Ingress) [BSI BER.4.6].

Protokoll: MCP-Revision 2026-07-28 (zustandslos) sowie über den Handshake 2024-11-05 bis
2025-11-25, Streamable HTTP unter `/mcp` (ADR 0001).

## Konfiguration

Alle Werte über Umgebungsvariablen mit Präfix `DBMCP_` (Vorlage: `.env.example`). Secrets haben
keine Defaults; bei ungültiger Konfiguration bricht der Start mit den Feldnamen ab.

| Variable | Default | Bedeutung |
|---|---|---|
| `DBMCP_DB_HOST` | – (Pflicht) | Datenbank-Host |
| `DBMCP_DB_PORT` / `DBMCP_DB_NAME` | `5432` / `titanic` | Port, Datenbankname |
| `DBMCP_DB_SSLMODE` | `verify-full` | `disable`, `require`, `verify-ca`, `verify-full` (`prefer` gibt es bewusst nicht) |
| `DBMCP_DB_SSLROOTCERT` | System-CAs | CA-Datei des DB-Zertifikats |
| `DBMCP_DB_APP_USER` / `DBMCP_DB_APP_PASSWORD` | `titanic_app` / – (Pflicht) | Rolle der festen Tools |
| `DBMCP_DB_SQL_USER` / `DBMCP_DB_SQL_PASSWORD` | `titanic_sql` / – (Pflicht) | Rolle von `run_sql` |
| `DBMCP_DB_OWNER_USER` / `DBMCP_DB_OWNER_PASSWORD` | `titanic_owner` / – (Pflicht für `db-init`) | Owner-Rolle, nur `db-init` |
| `DBMCP_DATA_FILE` | `data/titanic.csv` | CSV für den Seed |
| `DBMCP_API_KEYS_FILE` | – (Pflicht) | Pfad zur Key-Datei |
| `DBMCP_HOST` / `DBMCP_PORT` | `127.0.0.1` / `8000` | Bind-Adresse (im Container `0.0.0.0` setzen) und Port |
| `DBMCP_METRICS_PORT` | `9090` | Prometheus-Endpunkt |
| `DBMCP_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `DBMCP_ALLOWED_HOSTS` | `["localhost:*","127.0.0.1:*"]` | Erlaubte `Host`-Header auf `/mcp` (JSON-Liste) |
| `DBMCP_ALLOWED_ORIGINS` | `[]` | Erlaubte `Origin`-Header (Browser) |
| `DBMCP_RATE_LIMIT_PER_MINUTE` / `DBMCP_RATE_LIMIT_BURST` | `60` / `20` | Token-Bucket pro Key und Pod |
| `DBMCP_SQL_TIMEOUT_SECONDS` | `5` | Timeout für `run_sql` (max. 30) |
| `DBMCP_SQL_MAX_CONCURRENCY` | `4` | Parallele `run_sql`-Abfragen pro Pod |
| `DBMCP_CLIENT_IP_HEADER` | `x-forwarded-for` | Header mit der Client-IP (nur für Logs) |

## Architektur

```
Agent ─HTTPS─▶ Ingress (TLS) ─▶ Auth (API-Key → Agent, Team, Scopes) ─▶ Rate Limit ─▶ MCP-Tools
                                                  Services (Limits, PII-Regeln, SQL-Prüfung)
                                                  Repository ─TLS─▶ PostgreSQL (3 Rollen)
Querschnitt: JSON-Logs · Audit-Events · Prometheus-Metriken · /healthz · /readyz
```

Details: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Entscheidungen mit Alternativen:
[`docs/adr/`](docs/adr/) (0001 SDK/Protokoll, 0002 Datenbank, 0003 API-Keys, 0004 freies SQL,
0005 PII, 0006 Deployment, 0007 Dev Container). Regeln und Begründungen: [`CLAUDE.md`](CLAUDE.md),
Plan und BSI-Abgleich: [`docs/PLAN.md`](docs/PLAN.md).

## Annahmen

Jeweils mit Grund (ausführlich in `docs/PLAN.md`):

- **Zentraler Server über HTTP, Authentifizierung pro Agent** – ein Dienst hält Zugangsdaten,
  Regeln und Audit an einer Stelle; lokale stdio-Server bräuchten DB-Zugangsdaten bei jedem Team.
- **Berechtigung pro Agent, nicht pro Mensch** – kein Identity Provider vorgegeben; wer einen Agent
  betreibt, verantwortet dessen Nutzer.
- **Nur lesender Zugriff** – die Aufgabe nennt keine Schreibzugriffe; jeder Schreibzugriff würde
  die Angriffsfläche ohne Nutzen vergrößern.
- **`name`, `ticket`, `cabin` gelten als personenbezogen** – der Server soll auch für echte Daten
  taugen.
- **TLS am Ingress, Limit pro IP am Ingress** – Zertifikate und IP-Zustand verwaltet die Plattform.
- **Standardschnittstellen statt Produktbindung** (JSON auf stdout, Prometheus) – passt in jede
  bestehende Log- und Monitoring-Plattform; die CI ruft nur Make-Targets auf.
- **Zeitrahmen 8 Stunden eigene Arbeitszeit** – plattformabhängige Punkte sind offene Punkte mit
  konkretem nächsten Schritt.

## Bekannte Grenzen, offene Punkte und Ausblick

**Grenzen** (Details in `docs/SECURITY.md`): Identität nur pro Agent; Feld-Maskierung ist keine
Anonymisierung; Audit über stdout ist erst in der Log-Plattform revisionssicher; Systemkataloge
blockt nur der Validator; Rate Limit pro Pod; lokal ohne DB-TLS und mit Superuser-Owner.

**Begründete Abweichungen vom BSI:** A1 (kein TLS Ingress → Pod in der App), A2 (kein
Replay-Schutz für Bearer-Keys), A3 (kein zweiter Faktor für `pii`) – siehe `docs/PLAN.md`.

**Offene Punkte** (abhängig von der Plattform des Auftraggebers):
O1 automatische Rotation von Dienst-Zugangsdaten, O2 signierte Images (cosign) und
Admission-Policy, O3 automatische Abhängigkeits-Updates (Dependabot/Renovate), O4 produktionsnahe
Staging-Umgebung per OpenTofu/kind mit E2E-Test im Cluster.

**Ausblick:** OAuth 2.1 mit Nutzeridentität, verteiltes Rate Limiting, HPA und Tracing,
Managed-Postgres bzw. Operator, manipulationssicheres Audit-Log, Mindestgruppengrößen gegen
Re-Identifizierung, Pod-Neustart nach 24 h und MAC-Profile bei erhöhtem Schutzbedarf,
Secret-Scanning mit gitleaks.

## Datensatz: Quelle und Lizenz

`data/titanic.csv` entspricht der Datei `train.csv` des Kaggle-Wettbewerbs „Titanic – Machine
Learning from Disaster“ (891 Zeilen, 12 Spalten; 177 fehlende Alterswerte, 687 fehlende Kabinen,
2 fehlende Häfen). Bezogen von
`https://raw.githubusercontent.com/datasciencedojo/datasets/master/titanic.csv`,
SHA-256 `4a437fde05fe5264e1701a7387ac6fb75393772ba38bb2c9c566405af5af4bd7`.

**Lizenzlage, ehrlich:** Der Spiegel nennt keine eigene Lizenz. Die Daten gehen auf historische
Passagierlisten zurück und werden seit Jahren frei in Lehre und Kaggle-Wettbewerben genutzt; für
die Kaggle-Fassung gelten die Wettbewerbsbedingungen von Kaggle. Für diese Coding-Challenge
(nicht-kommerziell, zu Demonstrationszwecken) halte ich die Nutzung für unproblematisch; für einen
produktiven Einsatz wäre die Lizenz zu klären.

## KI-Nutzung

Der Code wurde mit Claude Code erzeugt; Steuerung über `CLAUDE.md` und `docs/PLAN.md`, Review durch
mich. Ablauf, Fehler und Korrekturen pro Etappe: [`docs/AI_USAGE.md`](docs/AI_USAGE.md).
