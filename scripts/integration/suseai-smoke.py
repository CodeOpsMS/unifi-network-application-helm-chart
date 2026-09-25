#!/usr/bin/env python3
"""Exercise one immutable chart package in an owned, disposable namespace.

MongoDB fixtures are deliberately outside charts/. Never restore production data here.
All Kubernetes mutations carry the explicit context; cleanup verifies namespace UID.
"""

import argparse
import base64
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote

import javaproperties

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from source_state import assert_unchanged, capture_source

if not __debug__:
    raise RuntimeError("Integration assertions require Python without -O or PYTHONOPTIMIZE")

MONGO_DIGEST = "sha256:b096b4cb9269f3ebcf363be63f1c50920f786879d03a1890347a3bf33f1f0df0"
MONGO_AMD64 = "sha256:afef081f9a06e810d1781214234b8c0dab77f9f694567bf24b193b78d445491e"
MONGO_IMAGE = f"mongo:7.0.41@{MONGO_DIGEST}"
UNIFI_VERSION = "10.6.106"
UNIFI_DIGEST = "sha256:5f5e76c95b5bd4becb0cdb1b96ef53a468e75ca0f7a096ca5c24fc30998b382a"
UNIFI_AMD64 = "sha256:59b41343c8ef2381181bb10e93c2b4d11501125731ed7ce25b35df46951ac9d2"


def property_fingerprint(contents):
    """Compare loaded Java property values, preserving meaningful whitespace."""
    properties = javaproperties.loads(contents)
    canonical = json.dumps(properties, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    return properties, hashlib.sha256(canonical).hexdigest()


class Smoke:
    def __init__(self, args):
        self.args = args
        self.source_state = capture_source()
        package_bytes = Path(args.package).read_bytes()
        self.run_id = time.strftime("%Y%m%d%H%M%S", time.gmtime()) + "-" + secrets.token_hex(3)
        self.ns = "unifi-smoke-" + self.run_id
        self.uid = None
        self.volumes = {}
        self.forwards = []
        self.sensitive = []
        self.out = Path(args.evidence).resolve() / self.run_id
        self.out.mkdir(parents=True, mode=0o700)
        self.private = Path(tempfile.mkdtemp(prefix="unifi-smoke-private-"))
        self.package = self.private / Path(args.package).name
        self.package.write_bytes(package_bytes)
        self.package.chmod(0o400)
        self.local_admin = None
        self.kube = ["kubectl", "--context", args.context, "--request-timeout=30s"]
        self.helm = [args.helm, "--kube-context", args.context]
        self.report = {"runId": self.run_id, "namespace": self.ns,
                       "package": Path(args.package).name,
                       "packageSha256": hashlib.sha256(package_bytes).hexdigest(),
                       "sourceCommit": self.source_state["commit"], "sourceState": self.source_state,
                       "checks": [], "passed": False, "cleanupPassed": False}

    def clean(self, text):
        for value in sorted(self.sensitive, key=len, reverse=True):
            text = text.replace(value, "[REDACTED]")
        return re.sub(r"mongodb(?:\+srv)?://[^\s\"']+", "mongodb://[REDACTED]", text)

    def run(self, cmd, data=None, check=True, timeout=1000):
        p = subprocess.run(cmd, input=data, text=True, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout)
        if check and p.returncode:
            raise RuntimeError(self.clean(f"Command failed ({p.returncode}): {' '.join(cmd)}\n{p.stdout}\n{p.stderr}"))
        return p.stdout if check or p.returncode == 0 else p.stdout + p.stderr

    def k(self, *cmd, **kwargs):
        return self.run(self.kube + list(cmd), **kwargs)

    def h(self, *cmd, **kwargs):
        return self.run(self.helm + list(cmd), **kwargs)

    def obj(self, *cmd):
        return json.loads(self.k(*cmd, "-o", "json"))

    def write(self, name, data):
        if not isinstance(data, str):
            data = json.dumps(data, indent=2) + "\n"
        (self.out / name).write_text(self.clean(data))

    def pass_check(self, name, **details):
        self.report["checks"].append({"name": name, "passed": True, **details})
        print("PASS: " + name, flush=True)
        self.write("summary.json", self.report)

    def create(self, obj):
        return json.loads(self.k("create", "-n", self.ns, "-f", "-", "-o", "json", data=json.dumps(obj)))

    def guard(self):
        if not self.uid or not re.fullmatch(r"unifi-smoke-\d{14}-[a-f0-9]{6}", self.ns):
            raise RuntimeError("Missing cleanup ownership identity")
        ns = self.obj("get", "namespace", self.ns)
        if ns["metadata"]["uid"] != self.uid or ns["metadata"]["labels"].get("unifi-test-run") != self.run_id:
            raise RuntimeError("Namespace UID or run label changed; refusing mutation")

    def record_volumes(self):
        for pvc in self.obj("get", "pvc", "-n", self.ns)["items"]:
            pv = pvc.get("spec", {}).get("volumeName")
            if pv:
                item = self.obj("get", "pv", pv)
                ref = item["spec"]["claimRef"]
                if ref["namespace"] != self.ns or ref["uid"] != pvc["metadata"]["uid"]:
                    raise RuntimeError("PV claim identity mismatch")
                self.volumes[pv] = {"pvUid": item["metadata"]["uid"], "pvcUid": ref["uid"],
                                    "claim": ref["name"]}

    def preflight(self):
        assert_unchanged(self.source_state, require_clean=True)
        self.report["kubernetesVersion"] = json.loads(self.k("get", "--raw=/version"))["gitVersion"]
        node = self.obj("get", "node", self.args.worker)
        assert node["metadata"]["labels"]["kubernetes.io/arch"] == "amd64"
        assert not node.get("spec", {}).get("unschedulable", False)
        assert any(c["type"] == "Ready" and c["status"] == "True" for c in node["status"]["conditions"])
        sc = self.obj("get", "storageclass", self.args.storage_class)
        assert sc.get("reclaimPolicy", "Delete") == "Delete", "Test storage must reclaim disposable volumes"
        metrics = self.k("top", "node", self.args.worker, "--no-headers").split()
        assert int(metrics[2].rstrip("%")) < 80 and int(metrics[4].rstrip("%")) < 85, "Worker is too busy for this integration run"
        self.report["workerUtilizationBeforeTest"] = {"cpuPercent": metrics[2], "memoryPercent": metrics[4]}
        ns = self.create({"apiVersion": "v1", "kind": "Namespace", "metadata": {
            "name": self.ns, "labels": {"unifi-test-run": self.run_id}}})
        self.uid = ns["metadata"]["uid"]
        self.report["namespaceUid"] = self.uid
        self.guard()
        self.create({"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "avx-check"},
                     "spec": {"nodeSelector": {"kubernetes.io/hostname": self.args.worker},
                              "automountServiceAccountToken": False, "restartPolicy": "Never",
                              "containers": [{"name": "check", "image": MONGO_IMAGE,
                                              "command": ["sh", "-ec", "grep -qw avx /proc/cpuinfo"],
                                              "resources": {"requests": {"cpu": "10m", "memory": "32Mi"},
                                                            "limits": {"cpu": "100m", "memory": "64Mi"}}}]}})
        self.k("wait", "-n", self.ns, "--for=jsonpath={.status.phase}=Succeeded", "pod/avx-check", "--timeout=5m")
        self.pass_check("worker-ready-and-avx", architecture="amd64")

    def secret(self, name, values):
        self.sensitive.extend(v for v in values.values() if len(v) > 8)
        return self.create({"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name},
                            "type": "Opaque", "stringData": values})

    def mongo(self):
        self.app_user = "unifi_smoke"
        self.app_pass = secrets.token_urlsafe(24) + "@:/$&?%"
        self.secret("mongo-credentials", {"root-user": "smoke_root", "root-password": secrets.token_urlsafe(32),
                                          "app-user": self.app_user, "app-password": self.app_pass})
        self.secret("unifi-database", {"username": quote(self.app_user, safe=""), "password": quote(self.app_pass, safe="")})
        init = """const admin = db.getSiblingDB('admin');
admin.createUser({user: process.env.APP_USER, pwd: process.env.APP_PASSWORD,
 roles: [{role:'clusterMonitor',db:'admin'}, ...['unifi','unifi_stat','unifi_audit','unifi_restore'].map(name => ({role:'dbOwner',db:name}))]});
"""
        self.create({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "mongo-init"}, "data": {"init.js": init}})
        self.create({"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": {"name": "mongo-data"},
                     "spec": {"accessModes": ["ReadWriteOnce"], "storageClassName": self.args.storage_class,
                              "resources": {"requests": {"storage": "2Gi"}}}})
        env = [{"name": name, "valueFrom": {"secretKeyRef": {"name": "mongo-credentials", "key": key}}}
               for name, key in [("MONGO_INITDB_ROOT_USERNAME", "root-user"), ("MONGO_INITDB_ROOT_PASSWORD", "root-password"),
                                 ("APP_USER", "app-user"), ("APP_PASSWORD", "app-password")]]
        self.create({"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "mongo"},
                     "spec": {"replicas": 1, "strategy": {"type": "Recreate"}, "selector": {"matchLabels": {"smoke-component": "mongo"}},
                              "template": {"metadata": {"labels": {"smoke-component": "mongo"}}, "spec": {
                                  "automountServiceAccountToken": False, "nodeSelector": {"kubernetes.io/hostname": self.args.worker},
                                  "containers": [{"name": "mongo", "image": MONGO_IMAGE, "args": ["--auth", "--bind_ip_all"],
                                                  "env": env, "ports": [{"name": "mongo", "containerPort": 27017}],
                                                  "resources": {"requests": {"cpu": "250m", "memory": "512Mi"}, "limits": {"cpu": "2", "memory": "1Gi"}},
                                                  "readinessProbe": {"tcpSocket": {"port": "mongo"}, "periodSeconds": 5},
                                                  "volumeMounts": [{"name": "data", "mountPath": "/data/db"}, {"name": "init", "mountPath": "/docker-entrypoint-initdb.d", "readOnly": True}]}],
                                  "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "mongo-data"}},
                                              {"name": "init", "configMap": {"name": "mongo-init"}}]}}}})
        self.create({"apiVersion": "v1", "kind": "Service", "metadata": {"name": "mongo"},
                     "spec": {"type": "ClusterIP", "selector": {"smoke-component": "mongo"}, "ports": [{"port": 27017, "targetPort": "mongo"}]}})
        self.k("rollout", "status", "-n", self.ns, "deployment/mongo", "--timeout=5m")
        self.mongo_eval("db=db.getSiblingDB('unifi'); db.smoke.insertOne({_id:'" + self.run_id + "',ok:true}); if(!db.smoke.findOne({_id:'" + self.run_id + "'}).ok) throw Error('read failure'); print('authenticated-write-read-ok');")
        self.mongo_eval("try {db.getSiblingDB('admin').auth(process.env.APP_USER, 'deliberately-wrong'); throw Error('BAD_AUTH_ACCEPTED');} catch(e) {if(e.message==='BAD_AUTH_ACCEPTED') throw e; print('wrong-password-rejected');}", auth=False)
        self.mongo_eval("try {db.getSiblingDB('unifi').smoke.insertOne({anonymous:true}); throw Error('ANONYMOUS_WRITE_ACCEPTED');} catch(e) {if(e.message==='ANONYMOUS_WRITE_ACCEPTED') throw e; print('anonymous-write-rejected');}", auth=False)
        self.pass_check("mongo-authentication-and-special-character-credentials")
        self.record_volumes()

    def mongo_eval(self, js, auth=True, root=False):
        if auth:
            user = "MONGO_INITDB_ROOT_USERNAME" if root else "APP_USER"
            password = "MONGO_INITDB_ROOT_PASSWORD" if root else "APP_PASSWORD"
            js = f"if(!db.getSiblingDB('admin').auth(process.env.{user},process.env.{password})) throw Error('auth failed'); " + js
        return self.k("exec", "-n", self.ns, "deployment/mongo", "--", "mongosh", "--quiet", "--eval", js)

    def values(self, name="unifi", **overrides):
        values = {"fullnameOverride": name, "externalDatabase": {"host": "mongo", "existingSecret": "unifi-database"},
                  "persistence": {"storageClass": self.args.storage_class, "retain": False},
                  "nodeSelector": {"kubernetes.io/hostname": self.args.worker}}
        for key, value in overrides.items():
            values[key] = {**values.get(key, {}), **value} if isinstance(value, dict) else value
        p = self.out / (name + "-values.json")
        p.write_text(json.dumps(values, indent=2))
        return str(p)

    def forward(self, resource, remote_port, namespace=None):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        f = (self.private / f"forward-{port}.log").open("w+")
        p = subprocess.Popen(self.kube + ["port-forward", "-n", namespace or self.ns, "--address", "127.0.0.1", resource, f"{port}:{remote_port}"], stdout=f, stderr=f)
        self.forwards.append((p, f))
        for _ in range(100):
            if p.poll() is not None:
                raise RuntimeError("Port-forward failed: " + (self.private / f"forward-{port}.log").read_text())
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    return port
            except OSError:
                time.sleep(0.2)
        raise RuntimeError("Port-forward did not become ready")

    def status(self, check_setup=False):
        port = self.forward("service/unifi", 8443)
        data = json.loads(self.run(["curl", "--noproxy", "*", "--silent", "--show-error", "--fail", "--insecure", "--max-time", "20", f"https://127.0.0.1:{port}/status"]))
        assert data["meta"]["up"] is True, data
        assert data["meta"]["server_version"] == UNIFI_VERSION, data
        self.write("status.json", data)
        if not check_setup:
            return
        # Fetch the management document directly; '/' need not return application HTML.
        html = self.run(["curl", "--noproxy", "*", "--silent", "--show-error", "--fail", "--insecure", "--location", "--max-time", "20", f"https://127.0.0.1:{port}/manage"])
        self.write("setup-entry.html", html)
        assert "<html" in html.lower() and '<base href="/setup/"' in html and 'id="root"' in html, "Setup HTML bootstrap missing"
        scripts = re.findall(r'<script\b[^>]*src="(/setup/static/js/main\.[^"/]+\.js)"', html)
        assert len(scripts) == 1, "Setup application bundle reference missing"
        asset_status = self.run(["curl", "--noproxy", "*", "--silent", "--show-error", "--fail", "--insecure", "--max-time", "30",
                                 "--output", os.devnull, "--write-out", "%{http_code}", f"https://127.0.0.1:{port}{scripts[0]}"])
        assert asset_status == "200", "Setup application bundle unavailable"
        self.report["setupAssetStatus"] = 200

    def complete_setup(self):
        """Finish the pinned application's local wizard without adopting devices.

        An unfinished wizard leaves UniFi in Factory Default state: its initial
        site can be recreated on restart. These endpoints implement the local
        setup flow originally verified with 10.6.101; each run checks completion.
        Test credentials, cookies and CSRF headers never enter public evidence.
        """
        port = self.forward("service/unifi", 8443)
        jar = self.private / "setup-cookies.txt"
        headers = self.private / "setup-headers.txt"
        password = secrets.token_urlsafe(32)
        ssh_password = secrets.token_urlsafe(32)
        self.sensitive.extend([password, ssh_password])

        def api(path, payload=None):
            lines = ["Content-Type: application/json"]
            if jar.exists():
                cookies = http.cookiejar.MozillaCookieJar(str(jar))
                cookies.load(ignore_discard=True, ignore_expires=True)
                for cookie in cookies:
                    self.sensitive.append(cookie.value)
                    if cookie.name == "csrf_token":
                        lines.append("X-Csrf-Token: " + cookie.value)
            headers.write_text("\n".join(lines) + "\n")
            cmd = ["curl", "--noproxy", "*", "--silent", "--show-error", "--fail-with-body", "--insecure", "--max-time", "30",
                   "--cookie", str(jar), "--cookie-jar", str(jar), "--header", "@" + str(headers)]
            if payload is not None:
                cmd += ["--data-binary", "@-"]
            cmd += [f"https://127.0.0.1:{port}" + path]
            response = self.run(cmd, data=json.dumps(payload) if payload is not None else None)
            if path.startswith("/api/"):
                result = json.loads(response)
                assert result.get("meta", {}).get("rc") == "ok", "Local setup API failed: " + path
                return result
            return response

        api("/setup/")
        api("/api/cmd/sitemgr", {"cmd": "add-default-admin", "name": "smokeadmin",
                                "email": "smoke-admin@example.invalid", "x_password": password})
        api("/api/set/setting/super_identity", {"name": "Disposable integration controller"})
        api("/api/set/setting/country", {"code": "276"})
        api("/api/set/setting/locale", {"timezone": "Europe/Berlin"})
        api("/api/set/setting/super_mgmt", {"autobackup_enabled": False, "backup_to_cloud_enabled": False})
        api("/api/set/setting/mgmt", {"x_ssh_username": "smokeadmin", "x_ssh_password": ssh_password})
        api("/api/cmd/system", {"cmd": "set-installed"})
        html = self.run(["curl", "--noproxy", "*", "--silent", "--show-error", "--fail", "--insecure", "--location", "--max-time", "30",
                         f"https://127.0.0.1:{port}/manage"])
        assert "<html" in html.lower() and '<base href="/setup/"' not in html, "Controller still serves initial setup"
        self.mongo_eval("if(db.getSiblingDB('unifi').device.countDocuments({})!==0) throw Error('Unexpected adopted devices');")
        self.status()
        self.pass_check("local-setup-completed-without-cloud-or-devices")
        self.local_admin = {"username": "smokeadmin", "password": password}
        self.check_local_admin()
        self.pass_check("local-admin-authentication-and-protected-api")

    def check_local_admin(self):
        """Use new sessions: reject an invalid password and verify a protected API."""
        if not self.local_admin:
            raise RuntimeError("Local administrator credentials are unavailable for authentication test")
        port = self.forward("service/unifi", 8443)
        jar = self.private / ("login-" + secrets.token_hex(4) + ".cookies")
        for valid in [False, True]:
            jar.unlink(missing_ok=True)
            password = self.local_admin["password"] if valid else secrets.token_urlsafe(32)
            self.sensitive.append(password)
            payload = {"username": self.local_admin["username"], "password": password}
            response = self.run(["curl", "--noproxy", "*", "--silent", "--show-error", "--insecure", "--max-time", "30",
                                 "--cookie-jar", str(jar), "--header", "Content-Type: application/json",
                                 "--data-binary", "@-", "--write-out", "\n%{http_code}",
                                 f"https://127.0.0.1:{port}/api/login"], data=json.dumps(payload))
            body, status = response.rsplit("\n", 1)
            result = json.loads(body)
            assert status == "200" if valid else status in {"400", "401", "403"}, "Login failed for a reason other than authentication"
            if not valid:
                assert re.search(r"invalid|auth|login|credential", result.get("meta", {}).get("msg", ""), re.I), "Negative login lacks an authentication error"
            assert (result.get("meta", {}).get("rc") == "ok") is valid, "Local login returned an unexpected result"
            if jar.exists():
                cookies = http.cookiejar.MozillaCookieJar(str(jar))
                cookies.load(ignore_discard=True, ignore_expires=True)
                self.sensitive.extend(cookie.value for cookie in cookies)
            identity_response = self.run(["curl", "--noproxy", "*", "--silent", "--show-error", "--insecure",
                                             "--max-time", "30", "--cookie", str(jar), "--write-out", "\n%{http_code}",
                                             f"https://127.0.0.1:{port}/api/self"])
            body, status = identity_response.rsplit("\n", 1)
            identity = json.loads(body)
            assert status == "200" if valid else status in {"401", "403"}, "Protected API did not enforce session authentication"
            assert (identity.get("meta", {}).get("rc") == "ok") is valid, "Protected API accepted an invalid session"
            if valid:
                assert any(item.get("name") == self.local_admin["username"] for item in identity.get("data", [])), "Authenticated identity differs from local test admin"
            jar.unlink(missing_ok=True)

    def runtime_images(self):
        result = {}
        for app, selector, expected in [("unifi", "app.kubernetes.io/instance=unifi", [UNIFI_DIGEST, UNIFI_AMD64]),
                                        ("mongo", "smoke-component=mongo", [MONGO_DIGEST, MONGO_AMD64])]:
            pods = self.obj("get", "pods", "-n", self.ns, "-l", selector)["items"]
            assert len(pods) == 1
            image_id = pods[0]["status"]["containerStatuses"][0]["imageID"]
            assert any(digest in image_id for digest in expected), image_id
            result[app] = {"image": pods[0]["spec"]["containers"][0]["image"], "imageID": image_id}
        self.report["runtimeImages"] = result

    def install(self):
        vals = self.values()
        args = ["unifi", str(self.package), "-n", self.ns, "-f", vals]
        rendered = self.h("template", *args)
        self.write("rendered.yaml", rendered)
        self.write("server-dry-run.log", self.k("create", "--dry-run=server", "--validate=strict", "-n", self.ns, "-f", "-", data=rendered))
        self.write("helm-dry-run.log", self.h("install", *args, "--dry-run=server"))
        self.pass_check("helm-and-kubernetes-server-dry-runs")
        print("Installing controller; startup budget 15 minutes", flush=True)
        self.write("install.log", self.h("install", *args, "--wait", "--timeout=16m", timeout=1020))
        self.status(check_setup=True)
        self.runtime_images()
        collections = json.loads(self.mongo_eval("print(JSON.stringify(db.getSiblingDB('unifi').getCollectionNames().filter(n => n !== 'smoke')));").splitlines()[-1])
        assert collections, "UniFi did not create application collections in MongoDB"
        self.report["applicationCollectionCount"] = len(collections)
        processes = self.k("exec", "-n", self.ns, "deployment/unifi", "--", "ps", "-eo", "args")
        assert "-Xms512M" in processes and "-Xmx1024M" in processes, "Runtime Java heap differs from chart values"
        self.report["runtimeHeapMiB"] = {"initial": 512, "max": 1024}
        self.record_volumes()
        self.pass_check(f"unifi-{UNIFI_VERSION}-setup-status-and-runtime-digests")

    def config_hashes(self):
        encoded = self.k("exec", "-n", self.ns, "deployment/unifi", "--", "base64", "/config/data/system.properties")
        contents = base64.b64decode("".join(encoded.split()), validate=True)
        self.latest_properties, properties_hash = property_fingerprint(contents)
        # Java Properties comments may contain a save timestamp. PKCS12 containers
        # may be rewritten with fresh random salts while preserving the certificate.
        certificate = self.k("exec", "-n", self.ns, "deployment/unifi", "--", "keytool", "-exportcert", "-rfc",
                             "-alias", "unifi", "-keystore", "/config/data/keystore", "-storepass", "aircontrolenterprise")
        der = base64.b64decode("".join(line for line in certificate.splitlines() if not line.startswith("---")))
        return {"normalizedPropertiesSha256": properties_hash,
                "certificateDerSha256": hashlib.sha256(der).hexdigest()}

    def persisted(self, hashes, volumes):
        current = self.config_hashes()
        if current != hashes:
            self.write("persistence-difference.json", {
                "expected": hashes, "actual": current,
                "changedPropertyKeys": sorted(key for key in self.baseline_properties.keys() | self.latest_properties.keys()
                                              if self.baseline_properties.get(key) != self.latest_properties.get(key))})
            raise AssertionError("Configuration values or keystore certificate changed; see persistence-difference.json")
        assert self.k("exec", "-n", self.ns, "deployment/unifi", "--", "cat", "/config/smoke-marker").strip() == self.run_id
        self.mongo_eval("const marker=db.getSiblingDB('smoke_validation').markers.findOne({_id:'" + self.run_id + "'}); if(!marker || !marker.ok) throw Error('database marker lost');", root=True)
        assert self.application_site_id() == self.site_id, "UniFi default site identity changed"
        self.record_volumes()
        assert self.volumes == volumes, "PVC/PV identity changed"
        self.status()
        if self.local_admin:
            self.check_local_admin()

    def persistence(self):
        hashes = self.config_hashes()
        self.baseline_properties = dict(self.latest_properties)
        self.write("config-hashes.json", hashes)
        self.k("exec", "-i", "-n", self.ns, "deployment/unifi", "--", "sh", "-c", "cat > /config/smoke-marker", data=self.run_id)
        # UniFi owns and may initialize/reset its application databases. Keep the
        # storage probe outside those databases, using only fixture-admin access.
        # The application itself continues to receive its restricted user only.
        self.mongo_eval("db.getSiblingDB('smoke_validation').markers.insertOne({_id:'" + self.run_id + "',ok:true});", root=True)
        self.site_id = self.application_site_id()
        self.report["applicationSiteIdSha256"] = hashlib.sha256(self.site_id.encode()).hexdigest()
        volumes = dict(self.volumes)
        for app in ["unifi", "mongo"]:
            self.guard()
            self.k("rollout", "restart", "-n", self.ns, "deployment/" + app)
            self.k("rollout", "status", "-n", self.ns, "deployment/" + app, "--timeout=16m", timeout=1020)
            # Allow the controller to reconnect after a separate database restart.
            for attempt in range(30):
                try:
                    self.persisted(hashes, volumes)
                    break
                except (RuntimeError, AssertionError):
                    if attempt == 29:
                        raise
                    time.sleep(2)
            self.pass_check(app + "-restart-persistence")
        vals = self.values(podAnnotations={"smoke-upgrade": self.run_id})
        self.write("upgrade.log", self.h("upgrade", "unifi", str(self.package), "-n", self.ns, "-f", vals, "--wait", "--timeout=16m", timeout=1020))
        self.persisted(hashes, volumes)
        self.pass_check("same-version-package-upgrade-persistence")

    def application_site_id(self):
        return self.mongo_eval("const site=db.getSiblingDB('unifi').site.findOne({name:'default'}); if(!site) throw Error('default site missing'); print(site._id.toString());").strip()

    def negative_cases(self):
        self.secret("missing-key", {"username": self.app_user})
        self.secret("bad-password", {"username": self.app_user, "password": secrets.token_urlsafe(24)})
        cases = [("missing-key", {"existingSecret": "missing-key"}),
                 ("bad-password", {"existingSecret": "bad-password"}),
                 ("unreachable-db", {"host": "absent-database.invalid"})]
        for name, db in cases:
            vals = self.values(name=name, externalDatabase=db,
                               probes={"startup": {"failureThreshold": 24, "periodSeconds": 5, "timeoutSeconds": 2}})
            self.h("install", name, str(self.package), "-n", self.ns, "-f", vals)
            deadline = time.monotonic() + 300
            observed = None
            while time.monotonic() < deadline:
                pods = self.obj("get", "pods", "-n", self.ns, "-l", "app.kubernetes.io/instance=" + name)["items"]
                if pods:
                    pod = pods[0]
                    assert not any(c["type"] == "Ready" and c["status"] == "True" for c in pod.get("status", {}).get("conditions", [])), name + " unexpectedly Ready"
                    statuses = pod.get("status", {}).get("containerStatuses", [])
                    state = statuses[0].get("state", {}) if statuses else {}
                    logs = self.k("logs", "-n", self.ns, "deployment/" + name, "--tail=150", check=False)
                    if name == "missing-key" and state.get("waiting", {}).get("reason") == "CreateContainerConfigError":
                        observed = "CreateContainerConfigError: missing Secret key"
                    elif name == "bad-password":
                        app_log = self.k("exec", "-n", self.ns, "deployment/" + name, "--", "sh", "-c", "tail -100 /config/logs/server.log 2>/dev/null", check=False)
                        if re.search(r"Authentication failed|MongoSecurityException|Exception authenticating", logs + app_log, re.I):
                            observed = "MongoDB authentication failure; Pod not Ready"
                    elif name == "unreachable-db" and "is not reachable, cannot proceed" in logs:
                        observed = "Database unreachable; Pod not Ready"
                    if observed:
                        break
                time.sleep(5)
            assert observed, "Expected negative signal missing: " + name
            if name == "bad-password":
                # Check for a delayed false-ready response after authentication errors.
                for _ in range(6):
                    time.sleep(5)
                    pods = self.obj("get", "pods", "-n", self.ns, "-l", "app.kubernetes.io/instance=" + name)["items"]
                    for pod in pods:
                        assert not any(c["type"] == "Ready" and c["status"] == "True" for c in pod.get("status", {}).get("conditions", [])), "Invalid credentials became Ready"
            self.record_volumes()
            self.pass_check("negative-" + name, signal=observed)
            self.h("uninstall", name, "-n", self.ns, "--wait", "--timeout=3m")

    def ingress(self):
        host = self.ns + ".invalid"
        cert, key = self.private / "tls.crt", self.private / "tls.key"
        self.run(["openssl", "req", "-x509", "-nodes", "-newkey", "rsa:2048", "-keyout", str(key), "-out", str(cert),
                  "-days", "1", "-subj", "/CN=" + host, "-addext", "subjectAltName=DNS:" + host])
        self.create({"apiVersion": "v1", "kind": "Secret", "type": "kubernetes.io/tls", "metadata": {"name": "ingress-tls"},
                     "data": {"tls.crt": base64.b64encode(cert.read_bytes()).decode(), "tls.key": base64.b64encode(key.read_bytes()).decode()}})
        vals = self.values(ingress={"enabled": True, "className": "nginx", "host": host, "tlsSecretName": "ingress-tls"})
        self.h("upgrade", "unifi", str(self.package), "-n", self.ns, "-f", vals, "--wait", "--timeout=16m", timeout=1020)
        selector = "app.kubernetes.io/name=rke2-ingress-nginx,app.kubernetes.io/component=controller"
        pods = self.obj("get", "pods", "-n", "kube-system", "-l", selector)["items"]
        pod = next(p for p in pods if any(c["type"] == "Ready" and c["status"] == "True" for c in p["status"]["conditions"]))
        port = self.forward("pod/" + pod["metadata"]["name"], 443, "kube-system")
        cmd = ["curl", "--noproxy", "*", "--silent", "--show-error", "--fail", "--max-time", "15", "--cacert", str(cert),
               "--resolve", f"{host}:{port}:127.0.0.1", f"https://{host}:{port}/status"]
        for attempt in range(30):
            try:
                data = json.loads(self.run(cmd))
                assert data["meta"]["up"] is True
                assert data["meta"]["server_version"] == UNIFI_VERSION
                break
            except (RuntimeError, json.JSONDecodeError, AssertionError):
                if attempt == 29:
                    raise
                time.sleep(2)
        self.pass_check("ingress-host-routing-trusted-test-certificate-and-https-backend")

    def cleanup(self):
        for proc, f in self.forwards:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            f.close()
        if self.uid:
            self.guard()
            self.record_volumes()
            self.write("events.log", self.k("get", "events", "-n", self.ns, "--sort-by=.lastTimestamp", check=False))
            self.write("resources.log", self.k("get", "pods,deployments,services,pvc", "-n", self.ns, "-o", "wide", check=False))
            self.write("controller.log", self.k("logs", "-n", self.ns, "deployment/unifi", "--tail=150", check=False))
            self.report["volumes"] = self.volumes
            self.k("delete", "namespace", self.ns, "--wait=false")
            self.k("wait", "--for=delete", "namespace/" + self.ns, "--timeout=5m")
            for name in self.volumes:
                self.k("wait", "--for=delete", "pv/" + name, "--timeout=5m")
            self.report["cleanupPassed"] = True
            print("PASS: owned namespace and all test PVs removed", flush=True)

    def execute(self):
        print(f"Testing package {self.report['packageSha256']} in {self.ns}", flush=True)
        error = None
        try:
            self.preflight()
            self.mongo()
            self.install()
            self.complete_setup()
            self.persistence()
            self.negative_cases()
            self.ingress()
            assert_unchanged(self.source_state, require_clean=True)
            self.report["passed"] = True
        except BaseException as exc:
            error = exc
            self.report["error"] = self.clean(str(exc))
            print("FAIL: " + self.clean(str(exc)), file=sys.stderr, flush=True)
        finally:
            try:
                self.cleanup()
            except BaseException as exc:
                self.report["cleanupError"] = self.clean(str(exc))
                self.report["passed"] = False
                error = error or exc
                print("CLEANUP FAILED: " + self.clean(str(exc)), file=sys.stderr, flush=True)
            finally:
                shutil.rmtree(self.private)
            try:
                assert_unchanged(self.source_state, require_clean=True)
            except RuntimeError as exc:
                error = error or exc
                self.report["sourceError"] = str(exc)
                self.report["passed"] = False
            self.write("summary.json", self.report)
        if error:
            return 1
        return 0 if self.report["passed"] and self.report["cleanupPassed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True)
    parser.add_argument("--context", default="suseai")
    parser.add_argument("--worker", default="laemk8saiworker2")
    parser.add_argument("--storage-class", default="harvester")
    parser.add_argument("--helm", default="helm")
    parser.add_argument("--evidence", default=".artifacts/integration")
    args = parser.parse_args()
    os.umask(0o077)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt("SIGTERM")))
    return Smoke(args).execute()


if __name__ == "__main__":
    sys.exit(main())
