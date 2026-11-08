# ADR 0006 – Deployment per Helm, lokal Docker Compose, In-Memory-Rate-Limit

Status: angenommen · Datum: 2026-11-08

## Kontext

Zielplattform ist Kubernetes, die Last ist gering und sporadisch. Die Plattform des Auftraggebers
(Registry, Secret-Management, Ingress, Monitoring) ist nicht bekannt. Zeitbudget: 8 Stunden eigene
Arbeitszeit.

## Entscheidung

- **Helm-Chart** `deploy/helm/database-mcp` mit gehärteten Defaults: 2 Replicas, eigener
  ServiceAccount ohne Token, `securityContext` (non-root, read-only Root-FS, keine Capabilities,
  seccomp `RuntimeDefault`), Probes, Ressourcenlimits, PodDisruptionBudget, NetworkPolicy mit
  Default-Deny, `db-init` als Helm-Hook (pre-install, pre-upgrade), Secrets nur als
  `existingSecret`-Referenzen, `values.schema.json`. Ingress und ServiceMonitor optional.
- Geprüft wird das Chart statisch: `helm lint --strict`, `helm template | kubeconform -strict`,
  `trivy config`.
- **Lokal Docker Compose** (Postgres, `db-init`, Server) mit Ende-zu-Ende-Test `make smoke`;
  dieselbe Abfolge läuft in der CI.
- **Rate Limit im Speicher** pro Pod (Token-Bucket pro `key_id`).

## Begründung

- Helm ist das verbreitete Paketformat; Werte pro Umgebung sind versioniert [BSI KONF.10.1.1].
- Secrets nur referenzieren: Das Chart funktioniert mit jedem Secret-Management (manuell,
  External Secrets, Sealed Secrets), ohne Secrets in Values-Dateien [BSI DEV.2.5].
- Compose statt lokalem Cluster: Der Cluster per OpenTofu/kind hätte 4–5 Stunden gekostet und war
  unter Windows das größte Risiko (offener Punkt O4).
- In-Memory-Rate-Limit: Bei geringer Last und zwei Replicas reicht das; ein verteiltes Limit
  (Redis) wäre eine zusätzliche, zu betreibende Komponente.

## Alternativen

- Kustomize: weniger Logik, aber keine Parameter-Validierung per Schema und kein Hook-Konzept für
  den Migrationsjob.
- OpenTofu + kind lokal: produktionsnäher, siehe O4.
- Redis für das Rate Limit: erst bei mehr Last oder vielen Replicas sinnvoll.

## Konsequenzen

- Ob das Chart in einem echten Cluster mit NetworkPolicy und Probes zusammenspielt, ist nicht
  automatisch getestet (O4).
- Effektives Rate Limit = Limit × Anzahl Replicas.
- Die NetworkPolicy erlaubt Egress zur Datenbank standardmäßig an jede Adresse auf dem DB-Port;
  `networkPolicy.databaseCidr` sollte auf den Adressbereich der Datenbank eingeschränkt werden.
