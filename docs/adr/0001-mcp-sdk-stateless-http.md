# ADR 0001 – Offizielles MCP-SDK, stateless Streamable HTTP

Status: angenommen · Datum: 2026-11-08

## Kontext

Agents mehrerer Teams greifen über das Netz zu; Zielplattform ist Kubernetes mit mehreren Replicas.
Die MCP-Spezifikation hat mit Revision 2026-07-28 ein zustandsloses Protokoll eingeführt; ältere
Clients sprechen noch die Revisionen mit `initialize`-Handshake.

## Entscheidung

- Offizielles Python-SDK `mcp` 2.x (installiert: 2.3.0), High-Level-API
  `mcp.server.mcpserver.MCPServer` (in 2.x der Nachfolger von `FastMCP`).
- Transport Streamable HTTP mit `stateless_http=True` und `json_response=True` unter `/mcp`.
- **Akzeptierte Protokollversionen** (aus `mcp.types.version` des installierten SDK):
  `2026-07-28` (zustandslos, ohne Handshake) sowie über den Handshake `2024-11-05`,
  `2025-03-26`, `2025-06-18` und `2025-11-25`. Das SDK leitet Requests anhand des Headers
  `MCP-Protocol-Version` an den passenden Pfad; beide Wege sind getestet
  (`test_modern_and_legacy_protocol_work`). Ältere Clients funktionieren also, z. B. für die Demo.
- **DNS-Rebinding-Schutz** über `TransportSecuritySettings` des SDK: Nur konfigurierte
  `Host`-Header (`TMCP_ALLOWED_HOSTS`) und `Origin`-Header (`TMCP_ALLOWED_ORIGINS`, Standard: keine)
  werden bedient. Getestet in `test_wrong_host_header_is_rejected`.
- Die Starlette-App bauen wir selbst: Der Session-Manager des SDK wird unter `/mcp` eingehängt, davor
  stehen die Authentifizierungs-Middleware des SDK mit unserem `TokenVerifier`, die SDK-Middleware
  `RequireAuthMiddleware` (401) und unser Rate Limit (429). Health-Routen liegen daneben ohne Auth.
- Werkzeuge liefern strukturierte Ausgaben (Pydantic-Modelle → `outputSchema` und
  `structuredContent`). `tools/list` wird pro Aufrufer gefiltert über eine `ServerMiddleware` des
  SDK; die Tools prüfen ihren Scope zusätzlich selbst.

## Begründung

- Offizielles SDK: folgt der Spezifikation am schnellsten, kein eigener Protokollcode.
- Stateless: beliebig viele Replicas ohne Sticky Sessions; ein Pod-Neustart verliert nichts.
- JSON-Antworten statt SSE: Die Tools streamen nichts; einfache Antworten funktionieren hinter
  jedem Proxy.
- Eigene App-Komposition statt `MCPServer.streamable_http_app(auth=...)`: Die SDK-Variante
  verlangt `AuthSettings` mit einer OAuth-`issuer_url`, die es hier nicht gibt. Eine erfundene URL
  wäre irreführend; mit den SDK-Bausteinen (`BearerAuthBackend`, `RequireAuthMiddleware`,
  `AuthContextMiddleware`) bleibt die Prüfung trotzdem die des SDK.

## Alternativen

- SSE-Transport: in der Spezifikation veraltet.
- Zustandsbehaftete Sessions: bräuchten Sticky Sessions oder einen gemeinsamen Session-Store.
- Low-Level-`Server` des SDK: mehr eigener Code für Schemas und Validierung.

## Konsequenzen

- Server-initiierte Anfragen (Sampling, Elicitation) sind im JSON-Modus nicht möglich; sie werden
  nicht gebraucht.
- Die Identität erreicht die Tools über den Request, den das SDK jedem Kontext anhängt
  (`ctx.request.scope["user"]`), und ein `contextvars`-Binding in der `ServerMiddleware`.
