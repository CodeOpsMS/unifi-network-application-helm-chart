#!/usr/bin/env python3
"""Validate chart behavior, failure modes, schemas and package boundaries."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile

import yaml


ROOT = Path(__file__).resolve().parent.parent
CHART = ROOT / "charts/unifi-network-application"
BASE = {"externalDatabase": {"host": "mongo.database.svc.cluster.local", "existingSecret": "unifi-db"}}
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
    service = object_of(documents, "Service")
    require(service["spec"]["selector"].get("app.kubernetes.io/component") == "controller",
            "Service must select only the controller component")
    for key, value in service["spec"]["selector"].items():
        require(pod["metadata"]["labels"].get(key) == value, f"Selector {key} misses controller")
    ports = {p["name"]: p for p in container["ports"]}
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
    for name in ["startupProbe", "readinessProbe", "livenessProbe"]:
        require(container[name]["httpGet"]["scheme"] == "HTTPS", f"{name} must use HTTPS")
        require(container[name]["httpGet"]["path"] == "/status", f"{name} must check /status")
    return deploy, container, env, service


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
    "probes": {"probes": {"startup": {"failureThreshold": 100}, "readiness": {"periodSeconds": 20},
                          "liveness": {"timeoutSeconds": 8}}, "terminationGracePeriodSeconds": 120},
    "extra-env": {"extraEnv": [{"name": "MY_SETTING", "value": "example"}, {"name": "MY_SECRET",
                              "valueFrom": {"secretKeyRef": {"name": "extra", "key": "item"}}}]},
    "image-override": {"image": {"repository": "example.invalid/custom", "tag": "test",
                                 "digest": "sha256:" + "a" * 64}},
    "placement": {"nodeSelector": {"kubernetes.io/arch": "amd64"},
                  "podAnnotations": {"example.invalid/test": "true"},
                  "env": {"TZ": "Europe/Berlin", "PUID": 1001, "PGID": 1002}},
}

NEGATIVE = {
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
    "negative-heap": {"java": {"maxHeapMiB": -1}},
    "fractional-heap": {"java": {"maxHeapMiB": 1.5}},
    "unsupported-memory-format": {"resources": {"limits": {"memory": "2000000000"}}},
    "implicit-ephemeral": {"persistence": {"enabled": False}},
    "ephemeral-with-claim": {"persistence": {"enabled": False, "testOnlyEphemeral": True,
                                             "existingClaim": "existing-config"}},
    "invalid-digest": {"image": {"digest": "sha256:invalid"}},
    "ingress-without-host": {"ingress": {"enabled": True, "className": "nginx", "tlsSecretName": "tls"}},
    "ingress-without-secret": {"ingress": {"enabled": True, "className": "nginx", "host": "unifi.invalid"}},
    "ingress-without-class": {"ingress": {"enabled": True, "className": "", "host": "unifi.invalid",
                                         "tlsSecretName": "tls"}},
    "invalid-probe": {"probes": {"startup": {"periodSeconds": 0}}},
    "db-provisioning-option": {"mongodb": {"enabled": True}},
    "node-port-on-clusterip": {"service": {"ports": {"https": {"nodePort": 30443}}}},
    "duplicate-node-port": {"service": {"type": "NodePort", "ports": {
        "https": {"nodePort": 30443}, "inform": {"nodePort": 30443}}}},
    "duplicate-service-port": {"service": {"ports": {"https": {"port": 8080}}}},
    "load-balancer-ip-on-clusterip": {"service": {"loadBalancerIP": "192.0.2.10"}},
    "duplicate-extra-env": {"extraEnv": [{"name": "EXTRA", "value": "a"}, {"name": "EXTRA", "value": "b"}]},
    "secret-and-literal-env": {"extraEnv": [{"name": "EXTRA", "value": "literal", "valueFrom": {
        "secretKeyRef": {"name": "existing", "key": "key"}}}]},
    "http-upstream": {"ingress": {"enabled": True, "host": "unifi.example.invalid", "tlsSecretName": "tls",
                                  "annotations": {"nginx.ingress.kubernetes.io/backend-protocol": "HTTP"}}},
}
for reserved in ["MONGO_USER", "MONGO_PASS", "MONGO_HOST", "MEM_LIMIT", "MEM_STARTUP", "PUID", "PGID", "TZ",
                 "FILE__MONGO_PASS", "FILE__MEM_LIMIT", "CERTFILE", "KEYFILE"]:
    NEGATIVE[f"reserved-env-{reserved.lower()}"] = {"extraEnv": [{"name": reserved, "value": "override"}]}


def check_case(name, documents):
    deploy, container, env, service = check_common(documents)
    if name == "default":
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
        require(deploy["spec"]["template"]["spec"]["nodeSelector"] == {"kubernetes.io/arch": "amd64"}, "Node selector")
        require(env["PUID"]["value"] == "1001" and env["PGID"]["value"] == "1002", "Container ownership")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--helm", default="helm")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    summary = {"helm": run([args.helm, "version", "--short"], check=True).stdout.strip(),
               "kubernetesSchemaVersion": "1.34.6", "schemaRevision": SCHEMA_COMMIT, "positive": [], "negative": []}
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
        summary["positive"].append(name)
    # Validate all generated objects against a fixed revision of strict Kubernetes schemas.
    manifests = [str(args.output / f"{name}.yaml") for name in POSITIVE]
    schema = run(["kubeconform", "-strict", "-summary", "-kubernetes-version", "1.34.6",
                  "-schema-location", SCHEMA_URL, *manifests])
    (args.output / "kubeconform.log").write_text(schema.stdout + schema.stderr)
    require(schema.returncode == 0, f"Kubernetes schema validation failed: {schema.stdout}{schema.stderr}")
    default_failure = run([args.helm, "template", "invalid", str(CHART)])
    require(default_failure.returncode != 0, "Missing mandatory external database values must fail")
    summary["negative"].append("empty-defaults")
    for name, changes in NEGATIVE.items():
        values = args.output / f"invalid-{name}-values.yaml"
        values.write_text(yaml.safe_dump(merge(BASE, changes)))
        result = run([args.helm, "template", "invalid", str(CHART), "--values", str(values)])
        require(result.returncode != 0, f"{name}: invalid configuration was accepted")
        (args.output / f"invalid-{name}.log").write_text(result.stdout + result.stderr)
        summary["negative"].append(name)
    package_dir = args.output / "package"
    package_dir.mkdir(exist_ok=True)
    result = run([args.helm, "package", str(CHART), "--destination", str(package_dir)], check=True)
    package, = package_dir.glob("*.tgz")
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
