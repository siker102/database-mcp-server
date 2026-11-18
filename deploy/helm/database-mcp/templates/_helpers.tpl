{{- define "database-mcp.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "database-mcp.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "database-mcp.labels" -}}
app.kubernetes.io/name: {{ include "database-mcp.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "database-mcp.selectorLabels" -}}
app.kubernetes.io/name: {{ include "database-mcp.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "database-mcp.image" -}}
{{- if .Values.image.digest -}}
{{ .Values.image.repository }}@{{ .Values.image.digest }}
{{- else -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- end -}}
{{- end -}}

{{/* Hardened container security context [BSI SYS.1.6.A17, SYS.1.6.A21, SYS.1.6.A23]. */}}
{{- define "database-mcp.containerSecurityContext" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "database-mcp.dbEnv" -}}
- name: DBMCP_DB_HOST
  value: {{ .Values.database.host | quote }}
- name: DBMCP_DB_PORT
  value: {{ .Values.database.port | quote }}
- name: DBMCP_DB_NAME
  value: {{ .Values.database.name | quote }}
- name: DBMCP_DB_SSLMODE
  value: {{ .Values.database.sslmode | quote }}
{{- if .Values.database.caSecret }}
- name: DBMCP_DB_SSLROOTCERT
  value: /etc/database-mcp/db-ca/ca.crt
{{- end }}
- name: DBMCP_DB_APP_USER
  value: {{ .Values.database.appUser | quote }}
- name: DBMCP_DB_SQL_USER
  value: {{ .Values.database.sqlUser | quote }}
- name: DBMCP_DB_APP_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.existingSecrets.database }}
      key: app-password
- name: DBMCP_DB_SQL_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.existingSecrets.database }}
      key: sql-password
{{- end -}}
