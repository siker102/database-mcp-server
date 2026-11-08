# ADR 0003 – API-Keys pro Agent mit Scopes

Status: angenommen · Datum: 2026-11-08

## Kontext

Wenige, bekannte Konsumenten (Agents anderer Teams) greifen maschinell zu. Ein Identity Provider
ist nicht vorgegeben. Jeder MCP-Client kann einen `Authorization`-Header senden.

## Entscheidung

- Bearer-Keys im Format `tmcp_<key_id>_<secret>`; Secret 32 Byte aus `secrets` (CSPRNG des
  Betriebssystems) [BSI BER.7.2, BER.7.5].
- Gespeichert wird nur `SHA-256(salt ‖ secret)` mit zufälligem Salt pro Key, Vergleich mit
  `hmac.compare_digest` [BSI DEV.3.4, TR-02102-1].
- Ein Key pro Agent, der Eintrag nennt `agent` und `team` [BSI BER.3.16].
- Jeder Key hat `expires_at`; höchstens 90 Tage mit `pii`, sonst 365 Tage. Eine längere Laufzeit
  verhindert den Serverstart [BSI BER.7.10, BER.7.13].
- Keys liegen als JSON-Datei vor (in Kubernetes ein gemountetes Secret), Pfad `TMCP_API_KEYS_FILE`.
- Die Prüfung implementiert das `TokenVerifier`-Protokoll des SDK; fehlende oder ungültige Keys
  beantwortet die SDK-Middleware mit 401 und `WWW-Authenticate: Bearer`, ohne Details
  [BSI DEV.3.3]. Erfolg und Misserfolg werden mit `key_id` protokolliert [BSI DET.3.1.1].
- Scopes `read`, `pii`, `sql`; alles nicht ausdrücklich Erlaubte ist verboten [BSI DEV.4.9].
- Rate Limit als Token-Bucket pro `key_id` im Speicher (429 mit `Retry-After`) [BSI KONF.15.3].

## Begründung

- Einfach zu betreiben, keine weitere Infrastruktur, mit jedem MCP-Client nutzbar.
- `key_id` im Key: Lookup ohne Vergleich gegen alle Hashes; Logs benennen den Key, ohne ihn
  preiszugeben. Präfix `tmcp_` macht Keys für Secret-Scanner erkennbar.
- Ein langsamer Passwort-Hash (bcrypt/Argon2) schützt schwache, menschliche Passwörter; bei
  256 Bit Zufall bringt er nichts, kostet aber pro Request Rechenzeit.

## Alternativen

- **OAuth 2.1 nach MCP-Autorisierungsprofil**: Standard für Nutzeridentität und kurzlebige Tokens,
  braucht aber einen Authorization Server. Migrationspfad: Das SDK bietet `AuthSettings` und
  `TokenVerifier`; ein JWT-Verifier ersetzt `ApiKeyVerifier`, die Scope-Logik bleibt.
- **mTLS zwischen Teams**: starke Maschinenidentität, braucht eine PKI.

## Konsequenzen und Abweichungen

- Identität nur pro Agent, nicht pro Mensch hinter dem Agent (Annahme im README).
- Abweichungen A2 (kein Replay-Schutz für Bearer-Token) und A3 (kein zweiter Faktor für `pii`) mit
  Begründung und Kompensation in `docs/PLAN.md` bzw. `docs/SECURITY.md`.
- Das Rate Limit gilt pro Pod; bei zwei Replicas ist das effektive Limit doppelt so hoch (ADR 0006).
- Rotation und Widerruf: Eintrag hinzufügen bzw. entfernen und ausrollen (`docs/RUNBOOK.md`).
