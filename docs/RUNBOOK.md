# Runbook

Betriebsabläufe für database-mcp. Installation siehe README, Abschnitt „Integration in einen
Kubernetes-Cluster“.

## Deployment und Update

```bash
helm upgrade --install database-mcp deploy/helm/database-mcp -n database-mcp -f my-values.yaml --wait
kubectl -n database-mcp rollout status deployment/database-mcp
```

Vor jedem Update läuft der Job `database-mcp-db-init` (Helm-Hook). Er ist idempotent; schlägt er
fehl, bricht `helm upgrade` ab und die alte Version läuft weiter. Logs:
`kubectl -n database-mcp logs job/database-mcp-db-init` (der Job wird nach Erfolg gelöscht).

Rollback: `helm -n database-mcp rollback database-mcp <revision>`.

## API-Keys

**Neuen Key ausgeben** (ein Key pro Agent [BSI BER.3.16]; `pii` nur auf begründeten Antrag
[BSI BER.4.3]):

```bash
kubectl -n database-mcp get secret database-mcp-api-keys -o jsonpath='{.data.api_keys\.json}' | base64 -d > api_keys.json
uv run python scripts/generate_api_key.py --agent report-bot --team analytics --scopes read --days 365 --append api_keys.json
kubectl -n database-mcp create secret generic database-mcp-api-keys --from-file=api_keys.json --dry-run=client -o yaml | kubectl apply -f -
kubectl -n database-mcp rollout restart deployment/database-mcp
shred -u api_keys.json   # lokale Kopie löschen
```

Der Key erscheint einmal auf stderr; Übergabe an das Team über einen sicheren Kanal (z. B.
Passwortmanager), nie per Chat oder E-Mail. Die Keys werden beim Start geladen, daher der Neustart.

**Rotation ohne Ausfall:** neuen Key für denselben Agent ausgeben (beide gelten parallel), das Team
stellt um, danach den alten Eintrag entfernen (siehe Widerruf). Höchstlaufzeit 90 Tage mit `pii`,
sonst 365 Tage; der Server startet nicht mit länger gültigen Keys.

**Widerruf / kompromittierter Key** [BSI BER.5.3, BER.5.14]: Eintrag mit der `key_id` aus
`api_keys.json` löschen, Secret ersetzen und neu starten (Befehle wie oben). Die `key_id` steht in
jedem Log-Event; den Zeitraum der Nutzung über die Audit-Events (`event=audit`, `key_id=...`)
prüfen.

**Regelmäßige Überprüfung** [BSI BER.4.4]: mindestens quartalsweise alle Einträge mit den Teams
abgleichen: Wird der Agent noch genutzt? Ist `pii` bzw. `sql` noch erforderlich?

## Datenbank

- Rollen `titanic_app` und `titanic_sql` stellt in der Regel das DB-Team bereit (SQL im README).
  Danach einmalig als Superuser: `ALTER ROLE titanic_sql SET temp_file_limit = '64MB';` sowie
  `ALTER ROLE titanic_sql SET statement_timeout = '5s';` und
  `ALTER ROLE titanic_sql SET default_transaction_read_only = on;`.
- DB-Passwort ändern: Passwort in der Datenbank setzen, Secret `database-mcp-db` ersetzen,
  `kubectl rollout restart`.
- Eine Datensicherung ist nicht nötig: Die Daten sind statisch und werden aus dem Image neu geladen.

## Logs und Metriken

- Logs: JSON auf stdout, `kubectl -n database-mcp logs deploy/database-mcp`. Wichtige Events:
  `audit` (jeder Tool-Aufruf), `auth_success`, `auth_failure` (mit `reason`), `scope_denied`,
  `rate_limited`, `sql_failed`, `tool_failed`, `readiness_failed`.
- Audit-Events (`event=audit`) in der Log-Plattform in einen eigenen Index mit eingeschränkten
  Rechten leiten [BSI DET.3.5].
- Metriken auf Port 9090: `tool_calls_total{tool,outcome,team}`, `tool_duration_seconds`,
  `auth_failures_total`, `rate_limited_total`. Sinnvolle Alarme: Anstieg von
  `auth_failures_total`, `tool_calls_total{outcome="error"}` > 0, häufiges `rate_limited_total`.

## Typische Fehler

| Symptom | Ursache | Lösung |
|---|---|---|
| Pod startet nicht, Log `invalid_configuration` | Pflicht-Variable fehlt oder ungültig (Feldname im Log) | Values/Secrets prüfen |
| Pod startet nicht, `KeyConfigError` | Key-Datei fehlt, ungültig, doppelte `key_id` oder zu lange Laufzeit | Key-Datei korrigieren |
| `/readyz` 503 | Datenbank nicht erreichbar, TLS-Zertifikat passt nicht zum Hostnamen | `readiness_failed` im Log, `database.caSecret`, NetworkPolicy `databaseCidr` |
| 401 trotz Key | Key abgelaufen, widerrufen oder Tippfehler | `auth_failure` mit `reason` im Log |
| 421 auf `/mcp` („Invalid Host header“) bzw. 403 („Invalid Origin header“) | `Host`- oder `Origin`-Header nicht erlaubt (DNS-Rebinding-Schutz des SDK) | Host in `server.allowedHosts` eintragen (der Ingress-Host wird automatisch ergänzt) |
| 429 | Rate Limit des Keys erreicht | `server.rateLimitPerMinute` prüfen oder Agent drosseln |
| `run_sql`: „time limit“ | Abfrage zu teuer | Abfrage vereinfachen; Timeout nur bewusst erhöhen |
