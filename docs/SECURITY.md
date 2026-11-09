# Sicherheitskonzept

Das Projekt orientiert sich an den Vorgaben des BSI (Grundschutz++, Kompendium-Bausteine SYS.1.6
und APP.4.4, TR-02102, TR-03183-2) auf dem Sicherheitsniveau „normal“. Es ist kein Nachweis einer
BSI-Konformität: Eine Zertifizierung setzt ein ISMS der Organisation voraus. Quellen und Links:
`docs/PLAN.md`, Abschnitt „Quellen für die BSI-Bezüge“.

## Schutzziele und Annahmen

- **Vertraulichkeit:** Personenbezogene Felder (`name`, `ticket`, `cabin`) nur mit Scope `pii`.
- **Integrität:** Kein Aufrufer kann Daten oder Schema ändern; der Server hat selbst keine
  Schreibrechte.
- **Verfügbarkeit:** Ein einzelner Agent oder eine teure Abfrage kann den Dienst nicht lahmlegen.
- **Nachvollziehbarkeit:** Jeder Aufruf ist einem Agent und Team zuordenbar (Audit-Log).
- TLS terminiert am Ingress; Lastschutz ohne gültigen Key (Limit pro IP) liegt am Ingress.

## Bedrohungsmodell

| Bedrohung | Beispiel | Maßnahmen |
|---|---|---|
| SQL-Injection über feste Tools | Filterwert `' OR 1=1` | Alle Werte als gebundene Parameter (SQLAlchemy Core), Pydantic-Validierung [BSI DEV.2.6, DEV.2.6.1] |
| Missbrauch von `run_sql` | `DELETE`, `COPY ... PROGRAM`, `pg_read_file`, Zugriff auf `passengers` | Vier Schichten (ADR 0004); jede Schicht einzeln getestet in `tests/security/` |
| Prompt Injection | Ein manipulierter Agent versucht, PII abzufragen oder Daten zu löschen | Server erzwingt alle Regeln selbst (Scopes, Limits, Rollen); Agent-Eingaben gelten als nicht vertrauenswürdig [BSI DEV.2.6.1] |
| Datenabfluss | Massenabfrage von Namen | `pii` nur auf Antrag, kurze Key-Laufzeit, Seitengröße max. 100, `run_sql` ohne PII, Audit-Log, Metriken [BSI DET.4.6] |
| Gestohlener Key | Key in einem Repository oder Log | Nur gesalzener Hash gespeichert, Keys nie geloggt, Ablaufdatum, Einzelwiderruf, Präfix für Secret-Scanner [BSI DEV.3.4, BER.5.3] |
| Brute Force auf Keys | Durchprobieren | 256 Bit Zufall; Limit pro IP am Ingress |
| Last durch teure Abfragen | Cross-Join, `pg_sleep` | Statement-Timeout, App-Timeout, `temp_file_limit`, Semaphore, Rate Limit [BSI KONF.15.3] |
| DNS-Rebinding | Browser-Seite spricht lokalen Server an | `Host`/`Origin`-Prüfung des SDK |
| Informationspreisgabe | Stacktrace in der Antwort | Generische Fehlermeldungen; Details nur im Log [BSI DEV.3.2, DEV.3.3] |
| Lieferkette | Manipuliertes Paket oder Image | `uv.lock` mit Hashes, Images per Digest, Actions per SHA, Trivy, pip-audit, SBOM [BSI DEV.4.3, DEV.4.4, DEV.4.5] |

## Zuordnung BSI-Anforderung → Maßnahme → Stelle im Repo

| BSI | Anforderung (kurz) | Maßnahme | Stelle |
|---|---|---|---|
| BER.4.1, DEV.2.3 | Geringste Rechte | Drei DB-Rollen, Server ohne Schreibrechte | `db/init_db.py`, `tests/integration/test_db_init.py` |
| BER.4.1.1 | Rollenbasierte Rechte | Scopes `read`/`pii`/`sql` | `auth.py`, `tools/common.py` |
| BER.3.16 | Keine Gruppenkonten | Ein Key pro Agent | `auth.py` (`ApiKeyEntry.agent`) |
| BER.7.10, BER.7.13 | Ablauf und Gültigkeit | `expires_at`, Höchstlaufzeit beim Start geprüft | `auth.py`, `tests/unit/test_auth.py` |
| BER.7.2, BER.7.5 | Schlüssellänge, Zufall | 256 Bit aus `secrets` | `auth.generate_key` |
| DEV.3.4 | Hash und Salt | `SHA-256(salt ‖ secret)` | `auth.hash_secret` |
| DEV.3.3 | Keine Interna in Fehlern | 401 ohne Details, generische Tool-Fehler | `tools/common.py`, `test_missing_or_wrong_key_is_401` |
| DEV.2.6, DEV.2.6.1 | Schutz vor Angriffsmustern, Eingabevalidierung | Pydantic, gebundene Parameter, SQL-Validator | `tools/definitions.py`, `services/sql_guard.py` |
| KONF.14.1, DEV.4.9 | Transportverschlüsselung, sichere Defaults | DB-Verbindung `verify-full` als Default; kein stilles Herabstufen | `db/engine.py`, `test_verify_full_does_not_downgrade` |
| KONF.9.1, KONF.15.3 | Ressourcen begrenzen | Timeouts, `temp_file_limit`, Semaphore, Rate Limit | `services/sql_runner.py`, `auth.py` |
| KONF.11.7, BER.4.6 | Schnittstellen begrenzen, Funktionen ohne Auth dokumentieren | `/metrics` separater Port; `/healthz`, `/readyz` ohne Auth dokumentiert | `app.py`, README |
| KONF.12.1.10 | HTTP-Sicherheitsheader | `nosniff`, `no-store` | `observability.RequestContextMiddleware` |
| DET.3.1, DET.3.1.1, DET.3.1.7 | Sicherheitsrelevante Ereignisse, Wer/Wann/Wo | Auth-Events, Audit-Event, `request_id`, Client-IP | `auth.py`, `observability.py` |
| DET.3.3 | Keine unnötigen Inhalte im Log | PII-Parameter maskiert, keine Ergebnisdaten | `observability.mask_parameters` |
| SYS.1.6.A7 | Logs außerhalb des Containers | JSON auf stdout | `observability.configure_logging` |
| SYS.1.6.A6, A17, A23 | Sichere Images, ohne Privilegien, unveränderlich | Offizielles Basis-Image per Digest, UID 10001, read-only Root-FS | `Dockerfile`, Helm-Chart |
| APP.4.4.A7, A9, A11 | Netztrennung, ServiceAccounts, Probes | NetworkPolicy, eigener SA ohne Token, Probes | `deploy/helm/database-mcp` |
| DEV.4.3, TR-03183-2 | SBOM | CycloneDX in der CI | `.github/workflows/ci.yml` |
| TEST.3.1, TEST.3.2 | Sicherheitstests, Testabdeckung | `tests/security/`, Coverage ≥ 85 % | `pyproject.toml` |

## Abweichungen vom BSI

Vollständige Begründung (Anforderung, Risiko, Kompensation, Restrisiko, Neubewertung) in
`docs/PLAN.md`, Abschnitt „Entscheidungen aus dem BSI-Abgleich“:

- **A1** – Kein TLS zwischen Ingress und Pod in der App [KONF.14.1]: Anforderung an die Plattform
  (Service Mesh mit mTLS); kompensiert durch NetworkPolicy.
- **A2** – Kein Replay-Schutz für Bearer-Keys [DEV.3.1]: MCP-Clients unterstützen keine signierten
  Anfragen; kompensiert durch TLS, nur lesende Tools, Ablaufdatum, Audit.
- **A3** – Kein zweiter Faktor für `pii` [BER.5.9]: Maschinen-Zugriff; kompensiert durch
  90-Tage-Höchstlaufzeit, Vergabe auf Antrag, regelmäßige Prüfung.

Offene Punkte O1–O4 (Rotation von Dienstkonten, signierte Images, automatische Updates,
Staging-Cluster) hängen von der Plattform des Auftraggebers ab.

## Bekannte Grenzen

- **Identität pro Agent, nicht pro Mensch.** Wer einen Agent mit `pii`-Key betreibt, verantwortet,
  wer ihn nutzt.
- **Feld-Maskierung ist keine Anonymisierung.** Über Alter, Fare und Klasse lassen sich einzelne
  Personen herausfiltern; `passenger_id` ist stabil.
- **Audit über stdout ist nicht revisionssicher.** Revisionssicherheit entsteht erst in der
  Log-Plattform (eigener Index, eingeschränkte Rechte) [BSI DET.3.5].
- **Systemkataloge** sind in PostgreSQL für jede Rolle lesbar; nur der Validator blockt sie. Sie
  enthalten keine Passagierdaten, aber Metadaten (Rollennamen, Sitzungen). Der Test-Review hat hier
  zwei Umgehungen gefunden und behoben (scope-genaue CTE-Auflösung, Typ-Allowlist für Casts; siehe
  Nachtrag in ADR 0004). Weitere Härtung wäre auf Datenbankseite möglich (Leserechte auf einzelne
  Katalog-Views für `PUBLIC` entziehen), betrifft aber alle Clients der Datenbank und ist Sache des
  DB-Teams.
- **Rate Limit pro Pod**: bei zwei Replicas effektiv doppelt so hoch.
- **Lokale Umgebung**: Compose-Postgres ohne TLS, `titanic_owner` ist dort Superuser, der
  Demo-Key liegt im Klartext in `secrets/demo_api_key` (gitignored). Nur für Entwicklung.
