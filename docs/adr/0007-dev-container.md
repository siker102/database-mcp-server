# ADR 0007 – Entwicklungsumgebung als Dev Container

Status: angenommen · Datum: 2026-11-08

## Kontext

Das Projekt soll auf einem Windows-PC mit möglichst wenig Voraussetzungen aufbau- und testbar sein
(nur Docker Desktop und VS Code). Reviewer sollen ohne Installationsanleitung für Python, uv, Helm
usw. starten können. Shell-Skripte und Makefile laufen unter Linux.

## Entscheidung

- Dev Container in `.devcontainer/` auf Basis des offiziellen Images
  `mcr.microsoft.com/devcontainers/python:3.14-trixie`, per Digest gepinnt.
- Offizielle Features (`docker-in-docker`, `kubectl-helm-minikube` nur für Helm) in exakten
  Versionen; `uv` aus dem offiziellen Image des Projekts, `kubeconform` als Release-Binary mit
  SHA-256-Prüfung im Dockerfile. Keine Community-Features [BSI DEV.4.2, DEV.4.4].
- docker-in-docker statt Zugriff auf den Docker-Daemon des Hosts.
- Die venv liegt außerhalb des Workspace (`UV_PROJECT_ENVIRONMENT`).
- `.gitattributes` erzwingt LF, damit Skripte aus einem Windows-Checkout im Linux-Container laufen.

## Begründung

- Alle arbeiten mit denselben, gepinnten Werkzeugversionen; „läuft bei mir“ entfällt.
- docker-in-docker: Compose-Stack und Testcontainer laufen im Container selbst, `localhost` und
  Ports verhalten sich unter Windows und in Codespaces gleich.
- venv außerhalb des Bind-Mounts: schneller unter Windows, kein Konflikt mit einer `.venv`, die
  auf dem Windows-Host angelegt wurde.

## Alternativen

- **Lokale Installation** (Python, uv, Helm unter Windows): mehr Voraussetzungen, Unterschiede
  zwischen Rechnern, Makefile läuft nicht nativ.
- **docker-outside-of-docker** (Socket des Hosts): nicht privilegiert, aber Testcontainer laufen
  dann neben dem Dev Container, Netzwerkadressen unterscheiden sich je Plattform; zudem ist der
  Host-Socket faktisch Root auf dem Docker-Host.
- **Community-Features** für Tools: bequem, aber Herkunft und Pflege weniger nachvollziehbar.

## Konsequenzen

- Der Dev Container läuft privilegiert (Voraussetzung für docker-in-docker). Das ist eine
  dokumentierte Ausnahme nur für die Entwicklungsumgebung [BSI SYS.1.6.A17]; das Server-Image und
  das Helm-Chart laufen ohne Privilegien.
- Werkzeugversionen müssen bewusst aktualisiert werden (siehe offener Punkt O3 in `docs/PLAN.md`).
