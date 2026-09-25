#!/usr/bin/env python3
"""Offline regressions for release evidence and disposable test ownership."""

import hashlib
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import release
from source_state import assert_unchanged, capture_source

ROOT = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHART_TESTS = load("chart_tests", "scripts/validate-chart.py")
SMOKE = load("smoke_tests", "scripts/integration/suseai-smoke.py")
UI = load("ui_tests", "scripts/integration/post-setup-ui.py")
STATE = {"commit": "a" * 40, "clean": True, "fingerprint": "b" * 64}


class ReleaseImageTests(unittest.TestCase):
    def test_chart_catalog_and_runtime_checks_use_the_same_image(self):
        chart = ROOT / "charts/unifi-network-application"
        meta = release.yaml.safe_load((chart / "Chart.yaml").read_text())
        image = release.yaml.safe_load((chart / "values.yaml").read_text())["image"]
        catalog = release.yaml.safe_load(meta["annotations"]["artifacthub.io/images"])
        self.assertEqual(meta["appVersion"], SMOKE.UNIFI_VERSION)
        self.assertTrue(image["tag"].startswith(meta["appVersion"] + "-ls"))
        self.assertEqual(image["digest"], SMOKE.UNIFI_DIGEST)
        self.assertEqual(catalog, [{"name": "unifi-network-application",
                                   "image": f"{image['repository']}:{image['tag']}@{image['digest']}"}])
        self.assertEqual({name for name in release.REQUIRED if name.startswith("unifi-")
                          and name.endswith("-setup-status-and-runtime-digests")},
                         {f"unifi-{SMOKE.UNIFI_VERSION}-setup-status-and-runtime-digests"})


class SourceStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="unifi-source-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test fixture")
        (self.root / "source").write_text("original")
        (self.root / ".gitignore").write_text("build/\n")
        self.commit()
        self.before = capture_source(self.root)

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, stderr=subprocess.STDOUT)

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")

    def test_clean_and_ignored_outputs(self):
        self.assertIs(self.before["clean"], True)
        (self.root / "build").mkdir()
        (self.root / "build/report.json").write_text("generated")
        self.assertEqual(self.before, assert_unchanged(self.before, self.root, require_clean=True))

    def test_tracked_edit_and_revert_cannot_reuse_dirty_evidence(self):
        (self.root / "source").write_text("changed test")
        dirty = capture_source(self.root)
        self.assertIs(dirty["clean"], False)
        self.assertNotEqual(dirty["fingerprint"], self.before["fingerprint"])
        (self.root / "source").write_text("original")
        with self.assertRaisesRegex(RuntimeError, "Commit source changes"):
            assert_unchanged(dirty, self.root, require_clean=True)

    def test_untracked_and_deleted_sources_change_evidence(self):
        (self.root / "new-test").write_text("new")
        with self.assertRaisesRegex(RuntimeError, "Source changed"):
            assert_unchanged(self.before, self.root)
        (self.root / "new-test").unlink()
        (self.root / "source").unlink()
        self.assertNotEqual(self.before, capture_source(self.root))

    def test_commit_change_invalidates_evidence(self):
        self.git("commit", "--allow-empty", "-qm", "another revision")
        self.assertEqual(self.before["fingerprint"], capture_source(self.root)["fingerprint"])
        with self.assertRaisesRegex(RuntimeError, "Source changed"):
            assert_unchanged(self.before, self.root)

    def test_symlink_target_is_recorded_without_following(self):
        link = self.root / "link"
        link.symlink_to("nonexistent-target")
        first = capture_source(self.root)
        link.unlink()
        link.symlink_to("another-nonexistent-target")
        self.assertNotEqual(first["fingerprint"], capture_source(self.root)["fingerprint"])

    def test_development_snapshot_can_be_dirty_but_not_released(self):
        (self.root / "source").write_text("development")
        dirty = capture_source(self.root)
        self.assertEqual(dirty, assert_unchanged(dirty, self.root))
        with self.assertRaises(RuntimeError):
            assert_unchanged(dirty, self.root, require_clean=True)


class ReleaseEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="unifi-evidence-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources = release.chart_sources()
        meta = release.yaml.safe_load(self.sources[release.CHART + "/Chart.yaml"])
        self.package = self.root / f"{release.CHART}-{meta['version']}.tgz"
        self.make_archive(self.sources)
        self.summary = self.root / "summary.json"
        self.validation = self.root / "validation.json"
        self.report = {"passed": True, "cleanupPassed": True, "sourceCommit": STATE["commit"],
                       "sourceState": STATE, "packageSha256": hashlib.sha256(self.package.read_bytes()).hexdigest(),
                       "checks": [{"name": name, "passed": True} for name in sorted(release.REQUIRED)]}
        self.lint = {"passed": True, "sourceCommit": STATE["commit"], "sourceState": STATE,
                     "helmVersions": sorted(release.HELM_VERSIONS),
                     "checks": {name: True for name in release.STATIC_CHECKS},
                     "matrix": [{"helm": "v" + version, "result": "pass", "positive": list(CHART_TESTS.POSITIVE),
                                 "negative": ["empty-defaults", *CHART_TESTS.NEGATIVE], "healthProbeBehaviorCases": 90,
                                 "kubernetesSchemaVersions": ["1.25.16", "1.34.6"],
                                 "schemaCases": {"1.34.6": list(CHART_TESTS.POSITIVE),
                                                 "1.25.16": ["default", "custom-routing", "static-pvc", "existing-pvc", "ephemeral-test"]},
                                 "package": {"name": self.package.name, "sha256": self.report["packageSha256"]},
                                 "schemaRevision": CHART_TESTS.SCHEMA_COMMIT}
                                for version in sorted(release.HELM_VERSIONS)]}
        self.capture = patch.object(release, "capture_source", return_value=STATE)
        self.capture.start()
        self.addCleanup(self.capture.stop)

    def make_archive(self, sources, duplicate=False):
        with tarfile.open(self.package, "w:gz") as archive:
            for name, data in sources.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            if duplicate:
                archive.addfile(member, io.BytesIO(data))

    def verify(self):
        self.summary.write_text(json.dumps(self.report))
        self.validation.write_text(json.dumps(self.lint))
        return release.verify(self.package, self.summary, self.validation)

    def test_complete_evidence_accepts_exact_package(self):
        _, digest = self.verify()
        self.assertEqual(digest, hashlib.sha256(self.package.read_bytes()).hexdigest())

    def test_false_and_string_booleans_are_rejected(self):
        for field in ["passed", "cleanupPassed"]:
            for invalid in [False, "false", 1, None]:
                with self.subTest(field=field, invalid=invalid):
                    self.report[field] = invalid
                    with self.assertRaises(ValueError):
                        self.verify()
                self.report[field] = True
        for invalid in [False, "false", 1]:
            self.report["checks"][0]["passed"] = invalid
            with self.assertRaises(ValueError):
                self.verify()

    def test_missing_or_duplicate_integration_check_is_rejected(self):
        last = self.report["checks"].pop()
        with self.assertRaisesRegex(ValueError, "integration evidence"):
            self.verify()
        self.report["checks"].extend([last, last])
        with self.assertRaises(ValueError):
            self.verify()

    def test_previous_application_version_evidence_is_rejected(self):
        current = f"unifi-{SMOKE.UNIFI_VERSION}-setup-status-and-runtime-digests"
        for check in self.report["checks"]:
            if check["name"] == current:
                check["name"] = "unifi-10.6.101-setup-status-and-runtime-digests"
        with self.assertRaisesRegex(ValueError, "integration evidence"):
            self.verify()

    def test_package_hash_must_match(self):
        self.report["packageSha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "tested bytes"):
            self.verify()

    def test_source_state_is_required_and_must_match(self):
        for evidence in [self.report, self.lint]:
            for state in [None, dict(STATE, clean=False), dict(STATE, fingerprint="c" * 64), dict(STATE, commit="c" * 40)]:
                evidence["sourceState"] = state
                with self.assertRaisesRegex(ValueError, "clean source"):
                    self.verify()
            evidence["sourceState"] = STATE
        with patch.object(release, "capture_source", return_value=dict(STATE, clean=False)):
            with self.assertRaisesRegex(ValueError, "dirty"):
                self.verify()

    def test_static_checks_and_both_matrices_are_mandatory(self):
        for invalid in [False, "false", 1, None]:
            self.lint["checks"]["helmUnitTests"] = invalid
            with self.assertRaisesRegex(ValueError, "Static checks"):
                self.verify()
        self.lint["checks"]["helmUnitTests"] = True
        matrix = self.lint["matrix"]
        for invalid in [[], matrix[:1], [matrix[0], matrix[0]]]:
            self.lint["matrix"] = invalid
            with self.assertRaises(ValueError):
                self.verify()

    def test_failed_or_incomplete_matrix_is_rejected(self):
        for field, invalid in [("result", "fail"), ("positive", []), ("negative", []),
                               ("healthProbeBehaviorCases", True), ("kubernetesSchemaVersions", []),
                               ("schemaRevision", "unknown"), ("schemaCases", {}), ("package", {})]:
            original = self.lint["matrix"][0][field]
            self.lint["matrix"][0][field] = invalid
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify()
            self.lint["matrix"][0][field] = original

    def test_archive_added_removed_modified_and_duplicate_members(self):
        for sources in [dict(self.sources, **{release.CHART + "/unexpected": b"extra"}),
                        {k: v for k, v in self.sources.items() if not k.endswith("values.yaml")},
                        dict(self.sources, **{release.CHART + "/values.yaml": b"tampered"})]:
            self.make_archive(sources)
            with self.assertRaises(ValueError):
                release.verify_archive(self.package, self.sources)
        self.make_archive(self.sources, duplicate=True)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            release.verify_archive(self.package, self.sources)

    def test_python_optimization_cannot_bypass_release_gates(self):
        self.report["passed"] = False
        self.summary.write_text(json.dumps(self.report))
        self.validation.write_text(json.dumps(self.lint))
        result = subprocess.run([sys.executable, "-O", "scripts/release.py", "verify", "--package", str(self.package),
                                 "--summary", str(self.summary), "--validation", str(self.validation)],
                                cwd=ROOT, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Integration/cleanup did not pass", result.stderr)


class RunnerSafetyTests(unittest.TestCase):
    def authentication_fixture(self, responses):
        temporary = tempfile.TemporaryDirectory(prefix="unifi-auth-test-")
        self.addCleanup(temporary.cleanup)
        smoke = SMOKE.Smoke.__new__(SMOKE.Smoke)
        smoke.private = Path(temporary.name)
        smoke.sensitive = []
        smoke.local_admin = {"username": "smokeadmin", "password": "offline-fixture-password"}
        smoke.forward = Mock(return_value=18443)
        smoke.run = Mock(side_effect=responses)
        return smoke

    def test_local_login_checks_rejected_and_authenticated_sessions(self):
        smoke = self.authentication_fixture([
            '{"meta":{"rc":"error","msg":"api.err.Invalid"}}\n400',
            '{"meta":{"rc":"error","msg":"api.err.LoginRequired"}}\n401',
            '{"meta":{"rc":"ok"}}\n200',
            '{"meta":{"rc":"ok"},"data":[{"name":"smokeadmin"}]}\n200',
        ])
        smoke.check_local_admin()
        self.assertEqual(smoke.run.call_count, 4)
        endpoints = [call.args[0][-1] for call in smoke.run.call_args_list]
        self.assertEqual(endpoints, ["https://127.0.0.1:18443/api/login", "https://127.0.0.1:18443/api/self"] * 2)

    def test_authentication_check_rejects_server_errors_and_wrong_identity(self):
        normal = ['{"meta":{"rc":"error","msg":"api.err.Invalid"}}\n400',
                  '{"meta":{"rc":"error"}}\n401', '{"meta":{"rc":"ok"}}\n200',
                  '{"meta":{"rc":"ok"},"data":[{"name":"smokeadmin"}]}\n200']
        for index, bad in [(0, '{"meta":{"rc":"error"}}\n500'),
                           (0, '{"meta":{"rc":"ok"}}\n200'),
                           (1, '{"meta":{"rc":"ok"}}\n200'),
                           (3, '{"meta":{"rc":"ok"},"data":[{"name":"another-admin"}]}\n200')]:
            responses = list(normal)
            responses[index] = bad
            with self.subTest(index=index), self.assertRaises(AssertionError):
                self.authentication_fixture(responses).check_local_admin()

    def test_optimized_smoke_is_rejected_before_cluster_access(self):
        for script in ["integration/suseai-smoke.py", "integration/post-setup-ui.py", "validate-persistence.py"]:
            result = subprocess.run([sys.executable, "-O", str(ROOT / "scripts" / script), "--help"],
                                    text=True, capture_output=True)
            with self.subTest(script=script):
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("without -O", result.stderr)

    def test_browser_completion_requires_run_boolean_and_every_check(self):
        valid = {"runId": "run", "passed": True, "checks": sorted(UI.REQUIRED_UI_CHECKS)}
        self.assertEqual(UI.check_completion(valid, "run"), sorted(UI.REQUIRED_UI_CHECKS))
        for changes in [{"runId": "other"}, {"passed": "true"}, {"passed": 1}, {"passed": False},
                        {"checks": valid["checks"][:-1]}, {"checks": "all"}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                UI.check_completion(dict(valid, **changes), "run")

    def test_namespace_guard_rejects_changed_uid_or_run_label(self):
        smoke = SMOKE.Smoke.__new__(SMOKE.Smoke)
        smoke.run_id = "20260911000000-abcdef"
        smoke.ns = "unifi-smoke-" + smoke.run_id
        smoke.uid = "owned-uid"
        namespace = {"metadata": {"uid": smoke.uid, "labels": {"unifi-test-run": smoke.run_id}}}
        smoke.obj = Mock(return_value=namespace)
        smoke.guard()
        for bad in [{"uid": "other", "labels": namespace["metadata"]["labels"]},
                    {"uid": smoke.uid, "labels": {"unifi-test-run": "other"}}]:
            smoke.obj.return_value = {"metadata": bad}
            with self.assertRaisesRegex(RuntimeError, "refusing mutation"):
                smoke.guard()

    def test_replacing_original_archive_does_not_change_private_fixture(self):
        with tempfile.TemporaryDirectory(prefix="unifi-package-test-") as temp:
            original = Path(temp) / "fixture.tgz"
            original.write_bytes(b"original tested bytes")
            args = SimpleNamespace(package=str(original), evidence=str(Path(temp) / "evidence"), context="unused", helm="helm")
            smoke = SMOKE.Smoke(args)
            try:
                original.write_bytes(b"a different package")
                self.assertEqual(smoke.package.read_bytes(), b"original tested bytes")
                self.assertEqual(smoke.report["packageSha256"], hashlib.sha256(smoke.package.read_bytes()).hexdigest())
                self.assertNotEqual(smoke.package, original)
                smoke.values = Mock(return_value="unused-values")
                smoke.h = Mock(return_value="")
                smoke.k = Mock(return_value="java -Xms512M -Xmx1024M")
                smoke.status = Mock()
                smoke.runtime_images = Mock()
                smoke.mongo_eval = Mock(return_value='["application_collection"]')
                smoke.record_volumes = Mock()
                with redirect_stdout(io.StringIO()):
                    smoke.install()
                for call in smoke.h.call_args_list:
                    self.assertIn(str(smoke.package), call.args)
                    self.assertNotIn(str(original), call.args)
            finally:
                shutil.rmtree(smoke.private)

    def test_browser_package_origin_uses_actual_metadata_and_archive(self):
        with tempfile.TemporaryDirectory(prefix="unifi-origin-test-") as temp:
            package = Path(temp) / "chart.tgz"
            sources = release.chart_sources()
            meta = release.yaml.safe_load(sources[release.CHART + "/Chart.yaml"])
            with tarfile.open(package, "w:gz") as archive:
                for name, data in sources.items():
                    member = tarfile.TarInfo(name)
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
            with patch.object(release, "chart_sources", return_value=sources) as lookup:
                actual, commit = release.package_source_commit(package)
                self.assertEqual(actual["version"], meta["version"])
                self.assertEqual(len(commit), 40)
                lookup.assert_called_once()
            sources[release.CHART + "/values.yaml"] = b"different source"
            with patch.object(release, "chart_sources", return_value=sources), self.assertRaisesRegex(ValueError, "mismatch"):
                release.package_source_commit(package)

    def test_reconnecting_browser_forward_closes_previous_log_handles(self):
        with tempfile.TemporaryDirectory(prefix="unifi-forward-test-") as temp:
            runner = UI.PostSetupUI.__new__(UI.PostSetupUI)
            runner.private = Path(temp)
            runner.ui_forward = None
            runner.ui_port = 18443
            runner.last_forward_attempt = 0
            runner.forward_attempt = 0
            runner.forwards = []
            runner.kube = ["unused-kubectl", "--context", "unused"]
            runner.ns = "unused-test-namespace"
            processes = [Mock(**{"poll.return_value": 1}) for _ in range(20)]
            logs = []
            with patch.object(UI.subprocess, "Popen", side_effect=processes), \
                    patch.object(UI.time, "monotonic", side_effect=range(1000, 1040)):
                for _ in processes:
                    runner.maintain_ui_forward()
                    self.assertEqual(len(runner.forwards), 1)
                    logs.append(runner.forwards[0][1])
            self.assertTrue(all(log.closed for log in logs[:-1]))
            logs[-1].close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
