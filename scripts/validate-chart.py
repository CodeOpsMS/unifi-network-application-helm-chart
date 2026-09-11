#!/usr/bin/env python3
"""Validate chart behavior, failure modes, schemas and package boundaries."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile

import yaml


ROOT = Path(__file__).resolve().parent.parent
CHART = ROOT / "charts/unifi-network-application"
BASE = {"externalDatabase": {"host": "mongo.database.svc.cluster.local", "existingSecret": "unifi-db"}}
# Application listener ports are fixed even when the public Service ports change.
LISTENERS = {"https": (8443, "TCP"), "inform": (8080, "TCP"), "stun": (3478, "UDP"),
             "discovery": (10001, "UDP"), "portal-http": (8880, "TCP"), "portal-https": (8843, "TCP"),
             "speedtest": (6789, "TCP"), "syslog": (5514, "UDP")}
SCHEMA_COMMIT = "b582a12a09aa9b5d1edad577a964c504130214fc"
SCHEMA_URL = ("https://raw.githubusercontent.com/yannh/kubernetes-json-schema/" + SCHEMA_COMMIT +
              "/{{.NormalizedKubernetesVersion}}-standalone{{.StrictSuffix}}/{{.ResourceKind}}{{.KindSuffix}}.json")


def merge(base, changes):
    result = copy.deepcopy(base)
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def run(command, **kwargs):
    return subprocess.run(command, text=True, capture_output=True, **kwargs)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def object_of(documents, kind):
    found = [doc for doc in documents if doc["kind"] == kind]
    require(len(found) == 1, f"Expected one {kind}, got {len(found)}")
    return found[0]


def check_common(documents):
    require(all(doc["kind"] in {"Deployment", "Service", "PersistentVolumeClaim", "Ingress"}
                for doc in documents), "Unexpected resource kind; chart must not provision a database or credentials")
    deploy = object_of(documents, "Deployment")
    pod = deploy["spec"]["template"]
    container, = pod["spec"]["containers"]
    require(deploy["spec"]["replicas"] == 1, "Controller requires exactly one replica")
    require(deploy["spec"]["strategy"]["type"] == "Recreate", "RWO update strategy must be Recreate")
    require(pod["spec"]["automountServiceAccountToken"] is False, "Service account token must not mount")
    require("initContainers" not in pod["spec"], "No bundled database bootstrap or wait container")
    require(pod["metadata"]["labels"].get("app.kubernetes.io/component") == "controller",
            "Controller component label missing")
    for key, value in deploy["spec"]["selector"]["matchLabels"].items():
        require(pod["metadata"]["labels"].get(key) == value, f"Deployment selector {key} misses its pod")
    service = object_of(documents, "Service")
    require(re.fullmatch(r"[a-z]([-a-z0-9]*[a-z0-9])?", service["metadata"]["name"]) is not None and
            len(service["metadata"]["name"]) <= 63, "Service name must satisfy the Kubernetes 1.25 DNS1035 baseline")
    require(service["spec"]["selector"].get("app.kubernetes.io/component") == "controller",
            "Service must select only the controller component")
    require(service["spec"]["selector"].get("app.kubernetes.io/instance") == "validation",
            "Service must select this release, not other controllers")
    for key, value in service["spec"]["selector"].items():
        require(pod["metadata"]["labels"].get(key) == value, f"Selector {key} misses controller")
    ports = {p["name"]: p for p in container["ports"]}
    require(len(ports) == len(container["ports"]), "Duplicate container port name")
    require({"https", "inform", "stun", "discovery"} <= ports.keys(), "Required application listener missing")
    require(set(ports) == {p["name"] for p in service["spec"]["ports"]}, "Service/container port sets differ")
    for name, port in ports.items():
        require(name in LISTENERS and (port["containerPort"], port["protocol"]) == LISTENERS[name],
                f"{name} must use its fixed application listener and protocol")
    for port in service["spec"]["ports"]:
        target = ports.get(port["targetPort"])
        require(target is not None, f"Service target {port['targetPort']} is not a named container port")
        require(target.get("protocol", "TCP") == port.get("protocol", "TCP"), "Service protocol mismatch")
    env = {item["name"]: item for item in container["env"]}
    require(len(env) == len(container["env"]), "Duplicate environment variable")
    for name in ["MONGO_USER", "MONGO_PASS"]:
        require("value" not in env[name] and "secretKeyRef" in env[name]["valueFrom"],
                f"{name} must use existing Secret reference")
    require("CERTFILE" not in env and "KEYFILE" not in env, "Unsupported certificate import variables")
    config, = [volume for volume in pod["spec"]["volumes"] if volume["name"] == "config"]
    mount, = [mount for mount in container["volumeMounts"] if mount["name"] == "config"]
    require(mount["mountPath"] == "/config" and not mount.get("readOnly", False), "Persistent config must be writable")
    claims = [doc for doc in documents if doc["kind"] == "PersistentVolumeClaim"]
    if claims:
        claim, = claims
        require(config.get("persistentVolumeClaim", {}).get("claimName") == claim["metadata"]["name"],
                "Created PVC must be mounted by the application")
    if service["spec"]["type"] == "ClusterIP":
        require("externalTrafficPolicy" not in service["spec"], "ClusterIP must omit external traffic policy")
        require(all("nodePort" not in port for port in service["spec"]["ports"]), "ClusterIP must omit nodePorts")
    if service["spec"]["type"] != "LoadBalancer":
        require("loadBalancerIP" not in service["spec"] and "loadBalancerSourceRanges" not in service["spec"],
                "Provider address and source ranges apply only to LoadBalancer")
    for ingress in [doc for doc in documents if doc["kind"] == "Ingress"]:
        rules = ingress["spec"]["rules"]
        require(len(rules) == 1 and len(rules[0]["http"]["paths"]) == 1, "Ingress exposes one management route")
        path = rules[0]["http"]["paths"][0]
        require(path["backend"]["service"] == {"name": service["metadata"]["name"], "port": {"name": "https"}},
                "Ingress must route to this release's named HTTPS Service port")
        require(path["pathType"] == "Prefix", "Ingress uses Prefix paths")
        require(ingress["spec"]["tls"][0]["hosts"] == [rules[0]["host"]], "TLS host must match the routing host")
    for name in ["startupProbe", "readinessProbe", "livenessProbe"]:
        command = container[name]["exec"]["command"]
        require(command[:2] == ["/bin/sh", "-ec"], f"{name} must fail when a command fails")
        require("https://127.0.0.1:8443/status" in command[2], f"{name} must check local HTTPS /status")
        require("jq --exit-status" in command[2], f"{name} must check semantic readiness in the JSON body")
        require(f"--max-time {max(1, container[name]['timeoutSeconds'] - 1)}" in command[2],
                f"{name} curl timeout must fit within the probe timeout")
    return deploy, container, env, service


def check_probe_behavior(container):
    """Execute the actual probe with an isolated curl stub and the real JSON parser."""
    cases = [("ready", '{"meta":{"up":true}}', 0, True),
             ("http-200-but-not-ready", '{"meta":{"up":false}}', 0, False),
             ("missing-readiness", '{"meta":{}}', 0, False),
             ("string-is-not-boolean", '{"meta":{"up":"true"}}', 0, False),
             ("number-is-not-boolean", '{"meta":{"up":1}}', 0, False),
             ("null-json", 'null', 0, False),
             ("malformed-json", 'not JSON', 0, False),
             ("empty-response", '', 0, False),
             ("curl-fails-after-body", '{"meta":{"up":true}}', 22, False),
             ("curl-timeout", '', 28, False)]
    with tempfile.TemporaryDirectory(prefix="unifi-probe-test-") as temporary:
        curl = Path(temporary) / "curl"
        arguments = Path(temporary) / "curl-arguments"
        curl.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" > "$MOCK_CURL_ARGUMENTS"\n'
                        'printf \'%s\' "$MOCK_STATUS"\nexit "${MOCK_CURL_EXIT:-0}"\n')
        curl.chmod(0o755)
        for probe in ["startupProbe", "readinessProbe", "livenessProbe"]:
            for name, response, exit_code, expected in cases:
                env = dict(os.environ, PATH=temporary + os.pathsep + os.environ["PATH"],
                           MOCK_STATUS=response, MOCK_CURL_EXIT=str(exit_code), MOCK_CURL_ARGUMENTS=str(arguments))
                arguments.unlink(missing_ok=True)
                result = run(container[probe]["exec"]["command"], env=env, timeout=10)
                require(arguments.exists(), f"{probe}/{name}: health command did not invoke curl")
                argv = arguments.read_text().splitlines()
                for flag in ["--fail", "--silent", "--show-error", "--insecure"]:
                    require(flag in argv, f"{probe}/{name}: curl requires {flag}")
                require(argv[-1] == "https://127.0.0.1:8443/status", f"{probe}: wrong curl target")
                require("--noproxy" in argv and argv[argv.index("--noproxy") + 1] == "*",
                        f"{probe}: local health checks must bypass proxy environment settings")
                require("--max-time" in argv and argv[argv.index("--max-time") + 1] ==
                        str(max(1, container[probe]["timeoutSeconds"] - 1)), f"{probe}: incorrect curl timeout")
                require((result.returncode == 0) == expected,
                        f"{probe}/{name}: wrong health result, exit={result.returncode}, stderr={result.stderr}")
    return len(cases) * 3


POSITIVE = {
    "default": {},
    "database": {"externalDatabase": {"host": "custom-mongo.example.invalid", "port": 27018,
                                       "database": "network", "authSource": "network_auth", "tls": True,
                                       "existingSecret": "custom-db", "usernameKey": "user", "passwordKey": "pass"}},
    "existing-pvc": {"persistence": {"existingClaim": "existing-config"}},
    "storage-class": {"persistence": {"storageClass": "harvester", "size": "8Gi"}},
    "ephemeral-test": {"persistence": {"enabled": False, "testOnlyEphemeral": True}},
    "delete-pvc": {"persistence": {"retain": False}},
    "node-port": {"service": {"type": "NodePort", "ports": {
        "https": {"port": 8444, "nodePort": 30443}, "inform": {"nodePort": 30080},
        "stun": {"nodePort": 30378}, "discovery": {"nodePort": 30001}}}},
    "load-balancer": {"service": {"type": "LoadBalancer", "loadBalancerIP": "192.0.2.10",
                                   "loadBalancerSourceRanges": ["192.0.2.0/24"],
                                   "annotations": {"example.invalid/provider": "test"},
                                   "externalTrafficPolicy": "Local"}},
    "optional-ports": {"service": {"portal": {"enabled": True}, "speedtest": {"enabled": True},
                                    "syslog": {"enabled": True}}},
    "ingress": {"ingress": {"enabled": True, "className": "nginx", "host": "unifi.example.invalid",
                            "tlsSecretName": "unifi-tls"}},
    "heap": {"java": {"initialHeapMiB": 768, "maxHeapMiB": 1536}, "resources": {
        "requests": {"memory": "2Gi", "cpu": "500m"}, "limits": {"memory": "3Gi", "cpu": "2"}}},
    "exact-headroom-boundary": {"resources": {"limits": {"memory": "1536Mi"}}},
    "cpu-equal-mixed-units": {"resources": {"requests": {"cpu": "1500m"}, "limits": {"cpu": "1.5"}}},
    "cpu-numeric-fraction": {"resources": {"requests": {"cpu": 0.299}, "limits": {"cpu": "300m"}}},
    "cpu-numeric-equal": {"resources": {"requests": {"cpu": 0.3}, "limits": {"cpu": "300m"}}},
    "cpu-minimum": {"resources": {"requests": {"cpu": "1m"}, "limits": {"cpu": 0.001}}},
    "static-pvc": {"persistence": {"storageClass": "-", "accessMode": "ReadWriteOncePod"}},
    "probes": {"probes": {"startup": {"failureThreshold": 100}, "readiness": {"periodSeconds": 20},
                          "liveness": {"timeoutSeconds": 8}}, "terminationGracePeriodSeconds": 120},
    "extra-env": {"extraEnv": [{"name": "MY_SETTING", "value": "example"}, {"name": "MY_SECRET",
                              "valueFrom": {"secretKeyRef": {"name": "extra", "key": "item"}}}]},
    "image-override": {"image": {"repository": "example.invalid/custom", "tag": "test",
                                 "digest": "sha256:" + "a" * 64}},
    "placement": {"nodeSelector": {"kubernetes.io/arch": "amd64"},
                  "podAnnotations": {"example.invalid/test": "true"},
                  "imagePullSecrets": [{"name": "registry-auth"}],
                  "tolerations": [{"key": "dedicated", "operator": "Equal", "value": "unifi", "effect": "NoSchedule"}],
                  "affinity": {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {
                      "nodeSelectorTerms": [{"matchExpressions": [
                          {"key": "kubernetes.io/arch", "operator": "In", "values": ["amd64"]}]}]}}},
                  "env": {"TZ": "Europe/Berlin", "PUID": 1001, "PGID": 1002}},
    "custom-routing": {"fullnameOverride": "network-controller", "service": {"type": "NodePort", "ports": {
        "https": {"port": 9443, "nodePort": 30443}, "inform": {"port": 9080, "nodePort": 30080},
        "stun": {"port": 4478, "nodePort": 30478}, "discovery": {"port": 11001, "nodePort": 31001}},
        "portal": {"enabled": True, "httpPort": 9880, "httpsPort": 9843, "httpNodePort": 30880, "httpsNodePort": 30843},
        "speedtest": {"enabled": True, "port": 7789, "nodePort": 30789},
        "syslog": {"enabled": True, "port": 6514, "nodePort": 30514}},
        "ingress": {"enabled": True, "host": "unifi.example.invalid", "tlsSecretName": "unifi-tls", "path": "/network",
                    "annotations": {"nginx.ingress.kubernetes.io/proxy-ssl-verify": "on",
                                    "nginx.ingress.kubernetes.io/proxy-ssl-secret": "unifi-ci/upstream-ca"}}},
    "cross-protocol-service-port": {"service": {"ports": {"stun": {"port": 8080}}}},
    "cross-protocol-nodeport": {"service": {"type": "NodePort", "ports": {
        "https": {"nodePort": 30443}, "stun": {"nodePort": 30443}}}},
    "numeric-name-suffix": {"nameOverride": "123-controller"},
    "readiness-success-threshold": {"probes": {"readiness": {"successThreshold": 2}, "startup": {"timeoutSeconds": 1}}},
}
POSITIVE["documented-loadbalancer"] = yaml.safe_load((ROOT / "examples/values-loadbalancer.yaml").read_text())
POSITIVE["documented-ingress"] = merge(POSITIVE["documented-loadbalancer"],
                                       yaml.safe_load((ROOT / "examples/values-ingress.yaml").read_text()))

NEGATIVE = {
    "numeric-fullname": {"fullnameOverride": "123-unifi"},
    "numeric-release-name": {},
    "missing-host": {"externalDatabase": {"host": ""}},
    "missing-secret": {"externalDatabase": {"existingSecret": ""}},
    "empty-user-key": {"externalDatabase": {"usernameKey": ""}},
    "empty-password-key": {"externalDatabase": {"passwordKey": ""}},
    "invalid-port": {"externalDatabase": {"port": 65536}},
    "invalid-service-port": {"service": {"ports": {"inform": {"port": 0}}}},
    "multiple-replicas": {"replicaCount": 2},
    "autoscaling": {"autoscaling": {"enabled": True}},
    "heap-order": {"java": {"initialHeapMiB": 2048}},
    "heap-headroom": {"resources": {"limits": {"memory": "1Gi"}}},
    "heap-headroom-one-mib-too-small": {"resources": {"limits": {"memory": "1535Mi"}}},
    "request-above-limit": {"resources": {"requests": {"memory": "3Gi"}}},
    "cpu-request-above-limit": {"resources": {"requests": {"cpu": "3"}, "limits": {"cpu": "2"}}},
    "cpu-millicores-above-limit": {"resources": {"requests": {"cpu": "501m"}, "limits": {"cpu": "0.5"}}},
    "cpu-numeric-above-limit": {"resources": {"requests": {"cpu": 0.301}, "limits": {"cpu": "300m"}}},
    "cpu-numeric-limit-too-small": {"resources": {"requests": {"cpu": "301m"}, "limits": {"cpu": 0.3}}},
    "zero-cpu": {"resources": {"requests": {"cpu": 0}}},
    "cpu-too-precise": {"resources": {"requests": {"cpu": "0.0001"}}},
    "negative-heap": {"java": {"maxHeapMiB": -1}},
    "fractional-heap": {"java": {"maxHeapMiB": 1.5}},
    "unsupported-memory-format": {"resources": {"limits": {"memory": "2000000000"}}},
    "implicit-ephemeral": {"persistence": {"enabled": False}},
    "ephemeral-flag-with-persistence": {"persistence": {"testOnlyEphemeral": True}},
    "ephemeral-with-claim": {"persistence": {"enabled": False, "testOnlyEphemeral": True,
                                             "existingClaim": "existing-config"}},
    "invalid-digest": {"image": {"digest": "sha256:invalid"}},
    "ingress-without-host": {"ingress": {"enabled": True, "className": "nginx", "tlsSecretName": "tls"}},
    "ingress-without-secret": {"ingress": {"enabled": True, "className": "nginx", "host": "unifi.invalid"}},
    "ingress-without-class": {"ingress": {"enabled": True, "className": "", "host": "unifi.invalid",
                                         "tlsSecretName": "tls"}},
    "invalid-probe": {"probes": {"startup": {"periodSeconds": 0}}},
    "startup-success-threshold": {"probes": {"startup": {"successThreshold": 2}}},
    "liveness-success-threshold": {"probes": {"liveness": {"successThreshold": 2}}},
    "probe-handler-override": {"probes": {"readiness": {"httpGet": {"path": "/", "port": 8443}}}},
    "db-provisioning-option": {"mongodb": {"enabled": True}},
    "node-port-on-clusterip": {"service": {"ports": {"https": {"nodePort": 30443}}}},
    "duplicate-node-port": {"service": {"type": "NodePort", "ports": {
        "https": {"nodePort": 30443}, "inform": {"nodePort": 30443}}}},
    "duplicate-service-port": {"service": {"ports": {"https": {"port": 8080}}}},
    "load-balancer-ip-on-clusterip": {"service": {"loadBalancerIP": "192.0.2.10"}},
    "load-balancer-ranges-on-nodeport": {"service": {"type": "NodePort", "loadBalancerSourceRanges": ["192.0.2.0/24"]}},
    "duplicate-optional-service-port": {"service": {"portal": {"enabled": True, "httpPort": 8080}}},
    "optional-nodeport-on-clusterip": {"service": {"speedtest": {"enabled": True, "nodePort": 30789}}},
    "duplicate-optional-nodeport": {"service": {"type": "NodePort", "ports": {"https": {"nodePort": 30443}},
                                              "portal": {"enabled": True, "httpsNodePort": 30443}}},
    "duplicate-extra-env": {"extraEnv": [{"name": "EXTRA", "value": "a"}, {"name": "EXTRA", "value": "b"}]},
    "secret-and-literal-env": {"extraEnv": [{"name": "EXTRA", "value": "literal", "valueFrom": {
        "secretKeyRef": {"name": "existing", "key": "key"}}}]},
    "literal-database-password": {"externalDatabase": {"password": "not-a-secret"}},
    "database-host-uri-injection": {"externalDatabase": {"host": "db.invalid/unifi?authSource=admin"}},
    "non-secret-env-source": {"extraEnv": [{"name": "EXTRA", "valueFrom": {
        "configMapKeyRef": {"name": "config", "key": "value"}}}]},
    "extra-env-without-value": {"extraEnv": [{"name": "EXTRA"}]},
    "extra-env-numeric-literal": {"extraEnv": [{"name": "EXTRA", "value": 123}]},
    "invalid-secret-key": {"externalDatabase": {"passwordKey": "invalid/key"}},
    "http-upstream": {"ingress": {"enabled": True, "host": "unifi.example.invalid", "tlsSecretName": "tls",
                                  "annotations": {"nginx.ingress.kubernetes.io/backend-protocol": "HTTP"}}},
}
for reserved in ["MONGO_USER", "MONGO_PASS", "MONGO_HOST", "MONGO_PORT", "MONGO_DBNAME", "MONGO_AUTHSOURCE",
                 "MONGO_TLS", "MEM_LIMIT", "MEM_STARTUP", "PUID", "PGID", "TZ", "CERTFILE", "KEYFILE"]:
    for variable in [reserved, "FILE__" + reserved]:
        NEGATIVE[f"reserved-env-{variable.lower()}"] = {"extraEnv": [{"name": variable, "value": "override"}]}


# A render crash is not proof that an invalid value was deliberately rejected.
TEMPLATE_ERRORS = {
    "numeric-fullname": "the generated Service name must start with a letter",
    "numeric-release-name": "the generated Service name must start with a letter",
    "missing-host": "externalDatabase.host is required; provision MongoDB separately",
    "missing-secret": "externalDatabase.existingSecret is required; create the credentials Secret separately",
    "heap-order": "java.initialHeapMiB must not exceed java.maxHeapMiB",
    "heap-headroom": "resources.limits.memory must allow at least 512 MiB above java.maxHeapMiB",
    "heap-headroom-one-mib-too-small": "resources.limits.memory must allow at least 512 MiB above java.maxHeapMiB",
    "request-above-limit": "resources.requests.memory must not exceed resources.limits.memory",
    "implicit-ephemeral": "persistence.enabled=false requires persistence.testOnlyEphemeral=true",
    "ephemeral-flag-with-persistence": "persistence.testOnlyEphemeral requires persistence.enabled=false",
    "ephemeral-with-claim": "persistence.existingClaim requires persistence.enabled=true",
    "ingress-without-host": "ingress.host is required when ingress.enabled=true",
    "ingress-without-secret": "ingress.tlsSecretName is required when ingress.enabled=true",
    "ingress-without-class": "ingress.className is required when ingress.enabled=true",
    "node-port-on-clusterip": "service nodePort values require type NodePort or LoadBalancer",
    "duplicate-node-port": "service contains duplicate nodePort TCP/30443",
    "duplicate-service-port": "service contains duplicate port TCP/8080",
    "load-balancer-ip-on-clusterip": "loadBalancerIP and loadBalancerSourceRanges require service.type=LoadBalancer",
    "load-balancer-ranges-on-nodeport": "loadBalancerIP and loadBalancerSourceRanges require service.type=LoadBalancer",
    "duplicate-optional-service-port": "service contains duplicate port TCP/8080",
    "optional-nodeport-on-clusterip": "service nodePort values require type NodePort or LoadBalancer",
    "duplicate-optional-nodeport": "service contains duplicate nodePort TCP/30443",
    "duplicate-extra-env": "extraEnv contains duplicate variable EXTRA",
    "http-upstream": "UniFi requires nginx.ingress.kubernetes.io/backend-protocol=HTTPS",
}
for name in ["cpu-request-above-limit", "cpu-millicores-above-limit", "cpu-numeric-above-limit",
             "cpu-numeric-limit-too-small"]:
    TEMPLATE_ERRORS[name] = "resources.requests.cpu must not exceed resources.limits.cpu"
for name, values in NEGATIVE.items():
    if name.startswith("reserved-env-"):
        TEMPLATE_ERRORS[name] = "extraEnv must not override chart-managed variable " + values["extraEnv"][0]["name"]


def check_rejection(name, changes, result):
    require(result.returncode != 0, f"{name}: invalid configuration was accepted")
    if name in TEMPLATE_ERRORS:
        require(TEMPLATE_ERRORS[name] in result.stderr,
                f"{name}: failed for an unrelated reason instead of the required safeguard: {result.stderr}")
    else:
        require("values don't meet the specifications of the schema" in result.stderr,
                f"{name}: expected a values schema rejection, got: {result.stderr}")
        # Helm's pinned majors use slash paths; retain support for dotted diagnostics.
        for key in changes:
            require(key in result.stderr, f"{name}: schema failure does not identify {key}: {result.stderr}")


def check_case(name, documents):
    deploy, container, env, service = check_common(documents)
    if name == "default":
        require(not any(doc["kind"] == "Ingress" for doc in documents), "Ingress must be opt-in")
        require(env["MEM_STARTUP"]["value"] == "512" and env["MEM_LIMIT"]["value"] == "1024", "Default Java heap")
        require(container["resources"]["limits"]["memory"] == "2Gi", "Default container headroom")
        require(container["resources"]["requests"]["memory"] == "1536Mi", "Default memory request")
        require(container["startupProbe"]["failureThreshold"] * container["startupProbe"]["periodSeconds"] == 900,
                "Startup budget must be 15 minutes")
        require(len(service["spec"]["ports"]) == 4, "Default has exactly four required ports")
        claim = object_of(documents, "PersistentVolumeClaim")
        require(claim["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep", "PVC retained by default")
    elif name == "database":
        for key, value in {"MONGO_HOST": "custom-mongo.example.invalid", "MONGO_PORT": "27018",
                           "MONGO_DBNAME": "network", "MONGO_AUTHSOURCE": "network_auth", "MONGO_TLS": "true"}.items():
            require(env[key]["value"] == value, f"Incorrect {key}")
        for variable, key in [("MONGO_USER", "user"), ("MONGO_PASS", "pass")]:
            require(env[variable]["valueFrom"]["secretKeyRef"] == {"name": "custom-db", "key": key}, "Secret key mapping")
    elif name == "existing-pvc":
        require(not any(d["kind"] == "PersistentVolumeClaim" for d in documents), "Existing PVC must not create PVC")
        require(any(v.get("persistentVolumeClaim", {}).get("claimName") == "existing-config"
                    for v in deploy["spec"]["template"]["spec"]["volumes"]), "Existing PVC reference missing")
    elif name == "ephemeral-test":
        require(not any(d["kind"] == "PersistentVolumeClaim" for d in documents), "Ephemeral test creates no PVC")
        require(any("emptyDir" in v for v in deploy["spec"]["template"]["spec"]["volumes"]), "Ephemeral test uses emptyDir")
    elif name == "delete-pvc":
        require(object_of(documents, "PersistentVolumeClaim")["metadata"].get("annotations", {}).get(
            "helm.sh/resource-policy") != "keep", "Explicit PVC deletion must be possible")
    elif name == "storage-class":
        claim = object_of(documents, "PersistentVolumeClaim")
        require(claim["spec"]["storageClassName"] == "harvester", "Explicit storage class")
        require(claim["spec"]["resources"]["requests"]["storage"] == "8Gi", "PVC size")
    elif name == "static-pvc":
        claim = object_of(documents, "PersistentVolumeClaim")
        require(claim["spec"]["storageClassName"] == "", "Static PVC must explicitly disable default StorageClass")
        require(claim["spec"]["accessModes"] == ["ReadWriteOncePod"], "Selected PVC access mode")
    elif name == "node-port":
        require(service["spec"]["type"] == "NodePort", "NodePort type")
        require({p["nodePort"] for p in service["spec"]["ports"]} == {30443, 30080, 30378, 30001}, "NodePorts preserved")
        require(any(p["port"] == 8444 for p in service["spec"]["ports"]), "Custom service port preserved")
    elif name == "load-balancer":
        require(service["spec"]["loadBalancerIP"] == "192.0.2.10", "LB address")
        require(service["spec"]["externalTrafficPolicy"] == "Local", "Traffic policy")
        require(service["spec"]["loadBalancerSourceRanges"] == ["192.0.2.0/24"], "LB source ranges")
        require(service["metadata"]["annotations"]["example.invalid/provider"] == "test", "Provider annotation")
    elif name == "optional-ports":
        require(len(service["spec"]["ports"]) == 8, "Four optional ports")
        require({p["port"] for p in service["spec"]["ports"]} >= {8880, 8843, 6789, 5514}, "Optional port numbers")
    elif name == "ingress":
        ingress = object_of(documents, "Ingress")
        require(ingress["metadata"]["annotations"]["nginx.ingress.kubernetes.io/backend-protocol"] == "HTTPS", "HTTPS upstream")
        require(ingress["spec"]["tls"] == [{"hosts": ["unifi.example.invalid"], "secretName": "unifi-tls"}], "Ingress TLS reference")
        require(ingress["spec"]["ingressClassName"] == "nginx", "Ingress class")
    elif name == "heap":
        require(env["MEM_STARTUP"]["value"] == "768" and env["MEM_LIMIT"]["value"] == "1536", "Custom JVM heap")
        require(container["resources"] == POSITIVE[name]["resources"], "Custom resource requests and limits preserved")
    elif name.startswith("cpu-"):
        for kind in ["requests", "limits"]:
            require(container["resources"][kind]["cpu"] == POSITIVE[name]["resources"][kind]["cpu"], "CPU values preserved")
    elif name == "probes":
        require(container["startupProbe"]["failureThreshold"] == 100, "Custom startup threshold")
        require(container["readinessProbe"]["periodSeconds"] == 20, "Custom readiness period")
        require(container["livenessProbe"]["timeoutSeconds"] == 8, "Custom liveness timeout")
        require(deploy["spec"]["template"]["spec"]["terminationGracePeriodSeconds"] == 120, "Custom termination grace")
    elif name == "extra-env":
        require(env["MY_SETTING"]["value"] == "example", "Extra string env")
        require(env["MY_SECRET"]["valueFrom"]["secretKeyRef"]["name"] == "extra", "Extra secret env")
    elif name == "image-override":
        require(container["image"] == "example.invalid/custom:test@sha256:" + "a" * 64, "Tag and digest rendered")
    elif name == "placement":
        pod = deploy["spec"]["template"]
        for field in ["nodeSelector", "tolerations", "affinity", "imagePullSecrets"]:
            require(pod["spec"][field] == POSITIVE[name][field], f"Custom {field} preserved")
        require(pod["metadata"]["annotations"] == {"example.invalid/test": "true"}, "Pod annotations")
        require(env["TZ"]["value"] == "Europe/Berlin", "Timezone")
        require(env["PUID"]["value"] == "1001" and env["PGID"]["value"] == "1002", "Container ownership")
    elif name == "custom-routing":
        require(all(doc["metadata"]["name"] == "network-controller" for doc in documents), "Consistent overridden names")
        expected = {"https": (9443, 30443), "inform": (9080, 30080), "stun": (4478, 30478),
                    "discovery": (11001, 31001), "portal-http": (9880, 30880), "portal-https": (9843, 30843),
                    "speedtest": (7789, 30789), "syslog": (6514, 30514)}
        require({port["name"]: (port["port"], port["nodePort"]) for port in service["spec"]["ports"]} == expected,
                "Custom public port and NodePort mappings")
        ingress = object_of(documents, "Ingress")
        require(ingress["spec"]["rules"][0]["http"]["paths"][0]["path"] == "/network", "Custom ingress path")
        require(ingress["metadata"]["annotations"]["nginx.ingress.kubernetes.io/proxy-ssl-verify"] == "on",
                "Operators must be able to enable trusted upstream certificate verification")
        require(ingress["metadata"]["annotations"]["nginx.ingress.kubernetes.io/proxy-ssl-secret"] == "unifi-ci/upstream-ca",
                "Custom upstream trust annotation")
    elif name == "cross-protocol-service-port":
        ports = {port["name"]: port for port in service["spec"]["ports"]}
        require(ports["stun"]["port"] == ports["inform"]["port"] == 8080, "TCP and UDP may share a Service port number")
    elif name == "cross-protocol-nodeport":
        ports = {port["name"]: port for port in service["spec"]["ports"]}
        require(ports["stun"]["nodePort"] == ports["https"]["nodePort"] == 30443, "TCP and UDP may share a NodePort number")
    elif name == "numeric-name-suffix":
        require(service["metadata"]["name"] == "validation-123-controller", "Only the effective Service name must start with a letter")
    elif name == "readiness-success-threshold":
        require(container["readinessProbe"]["successThreshold"] == 2, "Readiness may require repeated successes")
        require(container["startupProbe"]["timeoutSeconds"] == 1, "Minimum timeout is preserved")
    elif name.startswith("documented-"):
        require(service["spec"]["type"] == "LoadBalancer", "Documented device access uses a LoadBalancer")
        if name == "documented-ingress":
            object_of(documents, "Ingress")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--helm", default="helm")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").unlink(missing_ok=True)
    summary = {"helm": run([args.helm, "version", "--short"], check=True).stdout.strip(),
               "kubernetesSchemaVersion": "1.34.6", "kubernetesSchemaVersions": ["1.25.16", "1.34.6"],
               "schemaRevision": SCHEMA_COMMIT, "schemaCases": {}, "positive": [], "negative": [],
               "healthProbeBehaviorCases": 0}
    for name, changes in POSITIVE.items():
        values = args.output / f"{name}-values.yaml"
        values.write_text(yaml.safe_dump(merge(BASE, changes)))
        result = run([args.helm, "template", "validation", str(CHART), "--namespace", "unifi-ci",
                      "--kube-version", "1.34.6", "--values", str(values)])
        require(result.returncode == 0, f"{name}: rendering failed: {result.stderr}")
        manifest = args.output / f"{name}.yaml"
        manifest.write_text(result.stdout)
        documents = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
        check_case(name, documents)
        if name in {"default", "probes", "readiness-success-threshold"}:
            container = object_of(documents, "Deployment")["spec"]["template"]["spec"]["containers"][0]
            summary["healthProbeBehaviorCases"] += check_probe_behavior(container)
        summary["positive"].append(name)
    # Validate all generated objects against a fixed revision of strict Kubernetes schemas.
    manifests = [str(args.output / f"{name}.yaml") for name in POSITIVE]
    schema = run(["kubeconform", "-strict", "-summary", "-kubernetes-version", "1.34.6",
                  "-schema-location", SCHEMA_URL, *manifests])
    (args.output / "kubeconform.log").write_text(schema.stdout + schema.stderr)
    require(schema.returncode == 0, f"Kubernetes schema validation failed: {schema.stdout}{schema.stderr}")
    summary["schemaCases"]["1.34.6"] = list(POSITIVE)
    baseline = args.output / "kubernetes-1.25.16"
    baseline.mkdir(exist_ok=True)
    baseline_cases = ["default", "custom-routing", "static-pvc", "existing-pvc", "ephemeral-test"]
    for name in baseline_cases:
        result = run([args.helm, "template", "validation", str(CHART), "--namespace", "unifi-ci",
                      "--kube-version", "1.25.16", "--values", str(args.output / f"{name}-values.yaml")], check=True)
        (baseline / f"{name}.yaml").write_text(result.stdout)
        check_case(name, [doc for doc in yaml.safe_load_all(result.stdout) if doc])
    schema = run(["kubeconform", "-strict", "-summary", "-kubernetes-version", "1.25.16",
                  "-schema-location", SCHEMA_URL, *[str(baseline / f"{name}.yaml") for name in baseline_cases]])
    (baseline / "kubeconform.log").write_text(schema.stdout + schema.stderr)
    require(schema.returncode == 0, f"Kubernetes 1.25 baseline schema validation failed: {schema.stdout}{schema.stderr}")
    summary["schemaCases"]["1.25.16"] = baseline_cases
    default_failure = run([args.helm, "template", "invalid", str(CHART)])
    check_rejection("missing-host", {"externalDatabase": {"host": ""}}, default_failure)
    summary["negative"].append("empty-defaults")
    for name, changes in NEGATIVE.items():
        values = args.output / f"invalid-{name}-values.yaml"
        values.write_text(yaml.safe_dump(merge(BASE, changes)))
        release_name = "123-invalid" if name == "numeric-release-name" else "invalid"
        result = run([args.helm, "template", release_name, str(CHART), "--values", str(values)])
        check_rejection(name, changes, result)
        (args.output / f"invalid-{name}.log").write_text(result.stdout + result.stderr)
        summary["negative"].append(name)
    package_dir = args.output / "package"
    package_dir.mkdir(exist_ok=True)
    result = run([args.helm, "package", str(CHART), "--destination", str(package_dir)], check=True)
    chart_metadata = yaml.safe_load((CHART / "Chart.yaml").read_text())
    package = package_dir / f"{chart_metadata['name']}-{chart_metadata['version']}.tgz"
    with tarfile.open(package) as tar:
        members = tar.getnames()
        forbidden = [name for name in members if re.search(r"/(tests|ci|integration|build|scripts)/", name)]
        require(not forbidden, f"Test infrastructure leaked into chart archive: {forbidden}")
        chart_yaml = yaml.safe_load(tar.extractfile("unifi-network-application/Chart.yaml"))
        require(not chart_yaml.get("dependencies"), "Chart must not have database or other dependencies")
        require(not any("mongodb" in name.lower() for name in members), "Bundled MongoDB file in archive")
    package_render = run([args.helm, "template", "validation", str(package), "--namespace", "unifi-ci",
                          "--kube-version", "1.34.6", "--values", str(args.output / "default-values.yaml")], check=True)
    require(package_render.stdout == (args.output / "default.yaml").read_text(), "Packaged chart renders differently")
    summary["package"] = {"name": package.name, "sha256": hashlib.sha256(package.read_bytes()).hexdigest()}
    summary["result"] = "pass"
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"{summary['helm']}: {len(summary['positive'])} render cases, {len(summary['negative'])} rejected invalid cases, "
          "strict Kubernetes schemas and package boundary checks passed")


if __name__ == "__main__":
    main()
