#!/usr/bin/env python3
"""Keep one freshly configured test controller available for an explicit UI review.

Run after sourcing .tools/env.sh. The private ready file has phase=preparing until
the UI is available. Read its credentialsFile only in the authorized browser test.
Write {"runId": "...", "passed": true, "checks": [...]} to its completionFile
after the required browser checks, or passed=false to stop with failed evidence.
The fixed loopback port is restored automatically after a controller Pod restart.
No chart files, published package bytes or existing controllers are modified.
"""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time


SOURCE = Path(__file__).resolve().with_name("suseai-smoke.py")
SPEC = importlib.util.spec_from_file_location("unifi_smoke", SOURCE)
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)
from release import package_source_commit
REQUIRED_UI_CHECKS = {
    "initial-setup", "local-login", "dashboard", "devices-empty", "clients-empty", "wifi-settings",
    "network-settings", "local-admin", "controller-name-save", "logout-login", "post-restart-ui",
}


def private_json(path, contents):
    """Create a new owner-only file without replacing an existing handoff."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(contents, stream, indent=2)
        stream.write("\n")


def check_completion(contents, run_id):
    if not isinstance(contents, dict) or contents.get("runId") != run_id:
        raise ValueError("Browser completion has the wrong run identity")
    if contents.get("passed") is not True:
        raise ValueError("Browser review reported failure")
    checks = contents.get("checks")
    if not isinstance(checks, list) or not all(isinstance(item, str) for item in checks):
        raise ValueError("Browser completion checks must be an array of names")
    if not REQUIRED_UI_CHECKS <= set(checks):
        raise ValueError("Browser completion is missing required UI checks")
    # Never copy arbitrary browser-supplied text into the public summary.
    return sorted(REQUIRED_UI_CHECKS)


class PostSetupUI(SMOKE.Smoke):
    def __init__(self, args):
        self.admin_credentials = None
        super().__init__(args)
        self.ready_file = Path(args.ready_file).resolve()
        self.credentials_file = self.private / "admin-credentials.json"
        self.completion_file = self.private / "browser-complete.json"
        self.marker_file = self.private / "persistence-marker.json"
        self.ready_reserved = False
        self.ui_forward = None
        self.ui_port = None
        self.forward_attempt = 0
        self.last_forward_attempt = 0.0
        self.report["testPurpose"] = "post-setup-browser-review"
        self.report["suite"] = "post-setup-ui"
        self.report["testScriptSha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

    def run(self, cmd, data=None, check=True, timeout=1000):
        # Reuse the existing wizard implementation while exposing its random
        # test admin only to this runner's private handoff. Do not log its values.
        if data is not None and cmd and cmd[-1].endswith("/api/cmd/sitemgr"):
            payload = json.loads(data)
            if payload.get("cmd") == "add-default-admin":
                self.admin_credentials = {"username": payload["name"], "password": payload["x_password"]}
        return super().run(cmd, data=data, check=check, timeout=timeout)

    def reserve_ready(self):
        private_json(self.ready_file, {"runId": self.run_id, "phase": "preparing"})
        self.ready_reserved = True

    def remove_ready(self):
        if self.ready_reserved and self.ready_file.is_file():
            contents = json.loads(self.ready_file.read_text())
            if contents.get("runId") != self.run_id:
                raise RuntimeError("Ready file ownership changed; refusing to remove it")
            self.ready_file.unlink()

    def prepare_markers(self):
        self.baseline_hashes = self.config_hashes()
        self.baseline_properties = dict(self.latest_properties)
        self.k("exec", "-i", "-n", self.ns, "deployment/unifi", "--", "sh", "-c",
               "cat > /config/smoke-marker", data=self.run_id)
        self.mongo_eval("db.getSiblingDB('smoke_validation').markers.insertOne({_id:'" + self.run_id + "',ok:true});", root=True)
        self.site_id = self.application_site_id()
        self.record_volumes()
        self.baseline_volumes = dict(self.volumes)
        private_json(self.marker_file, {"runId": self.run_id, "siteId": self.site_id,
                                        "configMarker": "/config/smoke-marker",
                                        "configHashes": self.baseline_hashes})

    def maintain_ui_forward(self):
        if self.ui_forward is not None and self.ui_forward.poll() is None:
            return
        if self.ui_forward is not None:
            for process, log in list(self.forwards):
                if process is self.ui_forward:
                    log.close()
                    self.forwards.remove((process, log))
            self.ui_forward = None
        if time.monotonic() - self.last_forward_attempt < 1:
            return
        self.last_forward_attempt = time.monotonic()
        self.forward_attempt += 1
        log = (self.private / f"ui-forward-{self.forward_attempt}.log").open("w")
        command = self.kube + ["port-forward", "-n", self.ns, "--address", "127.0.0.1",
                               "service/unifi", f"{self.ui_port}:8443"]
        self.ui_forward = subprocess.Popen(command, stdout=log, stderr=log)
        self.forwards.append((self.ui_forward, log))

    def publish_ready(self):
        if not self.admin_credentials:
            raise RuntimeError("The completed wizard did not provide local test credentials")
        private_json(self.credentials_file, self.admin_credentials)
        self.local_admin = dict(self.admin_credentials)
        self.admin_credentials = None
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.ui_port = listener.getsockname()[1]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self.maintain_ui_forward()
            try:
                with socket.create_connection(("127.0.0.1", self.ui_port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("The browser loopback port-forward did not become ready")
        contents = {"runId": self.run_id, "phase": "ready", "namespace": self.ns, "namespaceUid": self.uid,
                    "context": self.args.context, "url": f"https://127.0.0.1:{self.ui_port}/manage",
                    "localPort": self.ui_port, "credentialsFile": str(self.credentials_file),
                    "completionFile": str(self.completion_file), "markerFile": str(self.marker_file),
                    "requiredChecks": sorted(REQUIRED_UI_CHECKS), "uiTimeoutSeconds": self.args.ui_timeout}
        if json.loads(self.ready_file.read_text()).get("runId") != self.run_id:
            raise RuntimeError("Ready file ownership changed")
        temporary = self.ready_file.with_name(self.ready_file.name + "." + self.run_id + ".tmp")
        private_json(temporary, contents)
        try:
            os.replace(temporary, self.ready_file)
        finally:
            temporary.unlink(missing_ok=True)
        print("READY: browser handoff is available in " + str(self.ready_file), flush=True)

    def wait_browser(self):
        deadline = time.monotonic() + self.args.ui_timeout
        while time.monotonic() < deadline:
            self.maintain_ui_forward()
            if self.completion_file.is_file():
                checks = check_completion(json.loads(self.completion_file.read_text()), self.run_id)
                self.report["browserChecks"] = checks
                self.pass_check("explicit-browser-review-completed", checkCount=len(checks))
                return
            time.sleep(1)
        raise TimeoutError("The browser review did not finish within its UI time window")

    def execute(self):
        error = None
        try:
            self.reserve_ready()
            meta, source_commit = package_source_commit(self.package)
            self.report["packageRelease"] = str(meta["version"])
            self.report["packageSourceCommit"] = source_commit
            self.preflight()
            self.mongo()
            self.install()
            if self.args.manual_setup:
                password = secrets.token_urlsafe(32)
                self.sensitive.append(password)
                self.admin_credentials = {"username": "smokeadmin", "password": password,
                                          "email": "smoke-admin@example.invalid"}
            else:
                self.complete_setup()
            self.prepare_markers()
            self.publish_ready()
            self.wait_browser()
            self.guard()
            self.persisted(self.baseline_hashes, self.baseline_volumes)
            self.mongo_eval("if(db.getSiblingDB('unifi').device.countDocuments({})!==0) throw Error('Unexpected adopted devices');")
            self.pass_check("post-browser-configuration-site-and-volume-persistence")
            SMOKE.assert_unchanged(self.source_state, require_clean=True)
            self.report["passed"] = True
        except BaseException as exc:
            error = exc
            self.report["error"] = self.clean(str(exc))
            print("FAIL: " + self.clean(str(exc)), file=sys.stderr, flush=True)
        finally:
            try:
                self.cleanup()
            except BaseException as exc:
                error = error or exc
                self.report["cleanupError"] = self.clean(str(exc))
                self.report["passed"] = False
                print("CLEANUP FAILED: " + self.clean(str(exc)), file=sys.stderr, flush=True)
            finally:
                try:
                    self.remove_ready()
                except BaseException as exc:
                    error = error or exc
                    self.report["handoffCleanupError"] = self.clean(str(exc))
                    self.report["passed"] = False
                finally:
                    shutil.rmtree(self.private)
            try:
                SMOKE.assert_unchanged(self.source_state, require_clean=True)
            except RuntimeError as exc:
                error = error or exc
                self.report["sourceError"] = str(exc)
                self.report["passed"] = False
            self.write("summary.json", self.report)
        return 1 if error else int(not (self.report["passed"] and self.report["cleanupPassed"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True)
    parser.add_argument("--context", default="suseai")
    parser.add_argument("--worker", default="laemk8saiworker2")
    parser.add_argument("--storage-class", default="harvester")
    parser.add_argument("--helm", default="helm")
    parser.add_argument("--evidence", default=".artifacts/post-setup-ui")
    parser.add_argument("--ready-file", default="/private/tmp/unifi-post-setup-ready.json")
    parser.add_argument("--ui-timeout", type=int, default=1800)
    parser.add_argument("--manual-setup", action="store_true", help="Complete the initial wizard in the browser using the private planned credentials")
    args = parser.parse_args()
    if not 1 <= args.ui_timeout <= 1800:
        parser.error("--ui-timeout must be between 1 and 1800 seconds")
    ready = Path(args.ready_file)
    root = SOURCE.parents[2]
    if not ready.is_absolute() or ready.resolve().is_relative_to(root):
        parser.error("--ready-file must be an absolute path outside the repository")
    if ready.exists() or ready.is_symlink():
        parser.error("--ready-file already exists; inspect its owner before removing it")
    os.umask(0o077)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt("SIGTERM")))
    return PostSetupUI(args).execute()


if __name__ == "__main__":
    sys.exit(main())
