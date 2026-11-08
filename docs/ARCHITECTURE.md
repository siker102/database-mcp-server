# Architektur

## Überblick

```
                  Kubernetes-Cluster
Agent ──HTTPS──▶ Ingress (TLS 1.3/1.2+PFS, Limit pro IP)
                    │  (mTLS über Service Mesh, Anforderung an die Plattform – Abweichung A1)
                    ▼
        ┌──────────────────────── Pod titanic-mcp (2 Replicas) ────────────────────────┐
        │ RequestContextMiddleware   request_id, Client-IP, Sicherheits-Header        │
        │ AuthenticationMiddleware   SDK + ApiKeyVerifier (gesalzener SHA-256, Ablauf)   │
        │ RequireAuthMiddleware      SDK: 401 ohne gültigen Key       (/mcp)            │
        │ RateLimitMiddleware        Token-Bucket pro key_id → 429    (/mcp)            │
        │ MCP SDK                    stateless Streamable HTTP, JSON-Antworten          │
        │   principal_middleware     Identität binden, tools/list nach Scope filtern    │
        │   Tools (dünn)             Scope-Prüfung, Audit, Metriken                     │
        │   Services                 Limits, PII-Regeln, SQL-Validator, SQL-Runner      │
        │   Repository               SQLAlchemy Core, gebundene Parameter               │
        │ /healthz /readyz (ohne Auth) · /metrics auf Port 9090 (nicht im Ingress)      │
        └──────────────┬─────────────────────────────────┬──────────────────────────────┘
                       │ TLS verify-full                 │ TLS verify-full, NullPool
                 Rolle titanic_app                 Rolle titanic_sql
                 SELECT passengers,                SELECT nur passengers_public
                 passengers_public
                       └──────────────▶ PostgreSQL ◀─────┘
                                          ▲
                       db-init (Helm-Hook-Job, Rolle titanic_owner):
                       Migrationen, Rechte, Seed aus data/titanic.csv
```

## Schichten

| Schicht | Ort | Aufgabe | Importiert `mcp`? |
|---|---|---|---|
| App | `app.py` | Starlette-App, Middleware-Kette, Lifespan, Health-Routen | ja |
| Auth | `auth.py` | Keys laden und prüfen, Scopes, Rate Limit | ja (SDK-Auth-Typen) |
| Tools | `tools/` | Eingaben (Typen/Grenzen), Scope-Prüfung, Audit, Metriken | ja |
| Services | `services/` | Logik, PII-Regeln, SQL-Validierung und -Ausführung | nein |
| Repository | `db/` | SQL, Engines, Migrationen, Seed | nein |
| Querschnitt | `observability.py` | JSON-Logs, Audit-Event, Prometheus-Metriken | nein |

## Ablauf eines Tool-Aufrufs

1. `RequestContextMiddleware` vergibt eine `request_id` und liest die Client-IP aus dem Header
   des Ingress (nur zum Loggen).
2. Die SDK-Authentifizierung ruft `ApiKeyVerifier.verify_token`: Format prüfen, `key_id`
   nachschlagen, `SHA-256(salt ‖ secret)` vergleichen, Ablauf prüfen; Ergebnis wird geloggt.
3. Ohne gültigen Key antwortet `RequireAuthMiddleware` mit 401; bei zu vielen Requests antwortet
   das Rate Limit mit 429.
4. Das SDK validiert die JSON-RPC-Nachricht und die Tool-Argumente gegen das Eingabeschema.
5. `principal_middleware` bindet den Aufrufer (Agent, Team, Scopes) an den Request-Kontext.
6. Das Tool prüft den Scope, ruft den Service; ohne `pii` liest das Repository aus der View.
7. Ergebnis als strukturiertes JSON; ein Audit-Event und Metriken werden geschrieben.

## Datenmodell

Tabelle `passengers` (alle Spalten), View `passengers_public` (ohne `name`, `ticket`, `cabin`).
Details und Rollen: `CLAUDE.md`, ADR 0002, ADR 0005.
