{{/* Modified by CodeOpsMS in 2026: names, component selectors, ports, and validation. */}}
{{- define "unifi-network-application.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "unifi-network-application.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "unifi-network-application.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "unifi-network-application.selectorLabels" -}}
app.kubernetes.io/name: {{ include "unifi-network-application.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: controller
{{- end -}}

{{- define "unifi-network-application.labels" -}}
{{ include "unifi-network-application.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end -}}

{{- define "unifi-network-application.image" -}}
{{- printf "%s:%s" .Values.image.repository .Values.image.tag -}}
{{- with .Values.image.digest -}}@{{ . }}{{- end -}}
{{- end -}}

{{/* Return enabled ports once for consistent Service/container configuration. */}}
{{- define "unifi-network-application.ports" -}}
{{- $ports := list -}}
{{- range $name := list "https" "inform" "stun" "discovery" -}}
{{- $values := index $.Values.service.ports $name -}}
{{- $containerPort := index (dict "https" 8443 "inform" 8080 "stun" 3478 "discovery" 10001) $name -}}
{{- $protocol := ternary "UDP" "TCP" (has $name (list "stun" "discovery")) -}}
{{- $ports = append $ports (dict "name" $name "port" $values.port "nodePort" $values.nodePort "containerPort" $containerPort "protocol" $protocol) -}}
{{- end -}}
{{- if .Values.service.portal.enabled -}}
{{- $ports = append $ports (dict "name" "portal-http" "port" .Values.service.portal.httpPort "nodePort" .Values.service.portal.httpNodePort "containerPort" 8880 "protocol" "TCP") -}}
{{- $ports = append $ports (dict "name" "portal-https" "port" .Values.service.portal.httpsPort "nodePort" .Values.service.portal.httpsNodePort "containerPort" 8843 "protocol" "TCP") -}}
{{- end -}}
{{- range $name := list "speedtest" "syslog" -}}
{{- $values := index $.Values.service $name -}}
{{- if $values.enabled -}}
{{- $ports = append $ports (dict "name" $name "port" $values.port "nodePort" $values.nodePort "containerPort" (ternary 5514 6789 (eq $name "syslog")) "protocol" (ternary "UDP" "TCP" (eq $name "syslog"))) -}}
{{- end -}}
{{- end -}}
{{- $ports | toJson -}}
{{- end -}}

{{- define "unifi-network-application.memoryMiB" -}}
{{- if not (regexMatch "^[1-9][0-9]*(Mi|Gi)$" .) -}}
{{- fail "resources memory must use a positive integer followed by Mi or Gi" -}}
{{- end -}}
{{- $number := regexFind "^[0-9]+" . | int64 -}}
{{- if hasSuffix "Gi" . -}}{{ mul $number 1024 }}{{- else -}}{{ $number }}{{- end -}}
{{- end -}}

{{- define "unifi-network-application.validate" -}}
{{- if not .Values.externalDatabase.host -}}{{ fail "externalDatabase.host is required; provision MongoDB separately" }}{{- end -}}
{{- if not .Values.externalDatabase.existingSecret -}}{{ fail "externalDatabase.existingSecret is required; create the credentials Secret separately" }}{{- end -}}
{{- if ne (int .Values.replicaCount) 1 -}}{{ fail "replicaCount must be 1; UniFi does not support chart-managed replicas" }}{{- end -}}
{{- if gt (int64 .Values.java.initialHeapMiB) (int64 .Values.java.maxHeapMiB) -}}
{{- fail "java.initialHeapMiB must not exceed java.maxHeapMiB" -}}
{{- end -}}
{{- $limit := include "unifi-network-application.memoryMiB" .Values.resources.limits.memory | int64 -}}
{{- $request := include "unifi-network-application.memoryMiB" .Values.resources.requests.memory | int64 -}}
{{- if lt $limit (add (int64 .Values.java.maxHeapMiB) 512) -}}
{{- fail "resources.limits.memory must allow at least 512 MiB above java.maxHeapMiB" -}}
{{- end -}}
{{- if gt $request $limit -}}{{ fail "resources.requests.memory must not exceed resources.limits.memory" }}{{- end -}}
{{- if and (not .Values.persistence.enabled) (not .Values.persistence.testOnlyEphemeral) -}}
{{- fail "persistence.enabled=false requires persistence.testOnlyEphemeral=true" -}}
{{- end -}}
{{- if and .Values.persistence.enabled .Values.persistence.testOnlyEphemeral -}}
{{- fail "persistence.testOnlyEphemeral requires persistence.enabled=false" -}}
{{- end -}}
{{- if and .Values.persistence.existingClaim (not .Values.persistence.enabled) -}}
{{- fail "persistence.existingClaim requires persistence.enabled=true" -}}
{{- end -}}
{{- $reserved := list "TZ" "PUID" "PGID" "MEM_STARTUP" "MEM_LIMIT" "MONGO_HOST" "MONGO_PORT" "MONGO_DBNAME" "MONGO_AUTHSOURCE" "MONGO_TLS" "MONGO_USER" "MONGO_PASS" "CERTFILE" "KEYFILE" -}}
{{- $seen := dict -}}
{{- range .Values.extraEnv -}}
{{- if has (trimPrefix "FILE__" .name) $reserved -}}{{ fail (printf "extraEnv must not override chart-managed variable %s" .name) }}{{- end -}}
{{- if hasKey $seen .name -}}{{ fail (printf "extraEnv contains duplicate variable %s" .name) }}{{- end -}}
{{- $_ := set $seen .name true -}}
{{- end -}}
{{- $servicePorts := dict -}}
{{- $nodePorts := dict -}}
{{- range (include "unifi-network-application.ports" . | fromJsonArray) -}}
{{- $key := printf "%s/%v" .protocol .port -}}
{{- if hasKey $servicePorts $key -}}{{ fail (printf "service contains duplicate port %s" $key) }}{{- end -}}
{{- $_ := set $servicePorts $key true -}}
{{- if .nodePort -}}
{{- if eq $.Values.service.type "ClusterIP" -}}{{ fail "service nodePort values require type NodePort or LoadBalancer" }}{{- end -}}
{{- $nodeKey := printf "%v" .nodePort -}}
{{- if hasKey $nodePorts $nodeKey -}}{{ fail (printf "service contains duplicate nodePort %s" $nodeKey) }}{{- end -}}
{{- $_ := set $nodePorts $nodeKey true -}}
{{- end -}}
{{- end -}}
{{- if and (ne .Values.service.type "LoadBalancer") (or .Values.service.loadBalancerIP .Values.service.loadBalancerSourceRanges) -}}
{{- fail "loadBalancerIP and loadBalancerSourceRanges require service.type=LoadBalancer" -}}
{{- end -}}
{{- if .Values.ingress.enabled -}}
{{- if not .Values.ingress.className -}}{{ fail "ingress.className is required when ingress.enabled=true" }}{{- end -}}
{{- if not .Values.ingress.host -}}{{ fail "ingress.host is required when ingress.enabled=true" }}{{- end -}}
{{- if not .Values.ingress.tlsSecretName -}}{{ fail "ingress.tlsSecretName is required when ingress.enabled=true" }}{{- end -}}
{{- if and (hasKey .Values.ingress.annotations "nginx.ingress.kubernetes.io/backend-protocol") (ne (index .Values.ingress.annotations "nginx.ingress.kubernetes.io/backend-protocol") "HTTPS") -}}
{{- fail "UniFi requires nginx.ingress.kubernetes.io/backend-protocol=HTTPS" -}}
{{- end -}}
{{- end -}}
{{- end -}}
