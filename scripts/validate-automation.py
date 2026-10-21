#!/usr/bin/env python3
"""Release lifecycle regressions: immutable versions, exact artifacts and retry safety."""

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("automation", Path(__file__).with_name("release-automation.py"))
automation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(automation)

COMMIT = "a" * 40
STATE = {"commit": COMMIT, "clean": True, "fingerprint": "b" * 64}
VERSION = "1.0.4"


def release_record(version=VERSION, draft=False, assets=None):
    return {"tag_name": version, "draft": draft,
            "assets": assets if assets is not None else [{"name": name} for name in automation.asset_names(version)]}


class PlanningTests(unittest.TestCase):
    def setUp(self):
        for name, value in [("chart_version", VERSION), ("capture_source", STATE),
                            ("releases", []), ("tag_commit", None)]:
            mock = patch.object(automation, name, return_value=value)
            setattr(self, name, mock.start())
            self.addCleanup(mock.stop)
        mock = patch.object(automation.release, "chart_sources", return_value={"Chart.yaml": b"same"})
        self.sources = mock.start()
        self.addCleanup(mock.stop)

    def test_new_version_builds(self):
        self.releases.return_value = [release_record("1.0.3")]
        self.assertEqual(automation.plan(), {"version": VERSION, "mode": "build"})

    def test_non_chart_merge_skips_published_version(self):
        self.releases.return_value = [release_record()]
        self.tag_commit.return_value = "c" * 40
        self.assertEqual(automation.plan()["mode"], "skip")

    def test_changed_chart_without_version_bump_fails(self):
        self.releases.return_value = [release_record()]
        self.tag_commit.return_value = COMMIT
        self.sources.side_effect = [{"values.yaml": b"old"}, {"values.yaml": b"new"}]
        with self.assertRaisesRegex(ValueError, "version bump"):
            automation.plan()

    def test_published_release_without_assets_is_not_treated_as_complete(self):
        self.releases.return_value = [release_record(assets=[])]
        self.tag_commit.return_value = COMMIT
        with self.assertRaisesRegex(ValueError, "incomplete"):
            automation.plan()

    def test_complete_draft_resumes_exact_source(self):
        self.releases.return_value = [release_record(draft=True)]
        self.tag_commit.return_value = COMMIT
        self.assertEqual(automation.plan()["mode"], "resume")

    def test_partial_draft_requires_original_artifact_retry(self):
        self.releases.return_value = [release_record(draft=True, assets=[{"name": "summary.json"}])]
        self.tag_commit.return_value = COMMIT
        with self.assertRaisesRegex(ValueError, "original test artifact"):
            automation.plan()

    def test_draft_for_other_source_cannot_be_retested_or_relabelled(self):
        self.releases.return_value = [release_record(draft=True)]
        self.tag_commit.return_value = "c" * 40
        with self.assertRaisesRegex(ValueError, "another commit"):
            automation.plan()

    def test_existing_tag_at_other_commit_is_not_replaced(self):
        self.tag_commit.return_value = "c" * 40
        with self.assertRaisesRegex(ValueError, "another commit"):
            automation.plan()

    def test_tag_created_before_interrupted_draft_can_be_recovered(self):
        self.tag_commit.return_value = COMMIT
        self.assertEqual(automation.plan()["mode"], "build")

    def test_older_version_is_rejected(self):
        self.releases.return_value = [release_record("1.0.5")]
        with self.assertRaisesRegex(ValueError, "greater"):
            automation.plan()

    def test_network_error_does_not_mean_new_release(self):
        self.releases.side_effect = RuntimeError("GitHub unavailable")
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            automation.plan()

    def test_dirty_sources_are_rejected(self):
        self.capture_source.return_value = dict(STATE, clean=False)
        with self.assertRaisesRegex(ValueError, "clean"):
            automation.plan()

    def test_version_input_cannot_be_a_ref_or_option(self):
        for version in ["--force", "main", "1.0", "1.0.4\nmode=skip", "01.0.4", "1.0.4-rc.1", 104]:
            with self.subTest(version=version), self.assertRaises(ValueError):
                automation.version_key(version)


class StagingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        for name in automation.asset_names(VERSION):
            (self.directory / name).write_bytes((name + " tested").encode())
        self.assets = [{"name": name, "digest": "sha256:" + hashlib.sha256((self.directory / name).read_bytes()).hexdigest()}
                       for name in sorted(automation.asset_names(VERSION))]
        for owner, name, value in [(automation, "chart_version", VERSION), (automation, "capture_source", STATE),
                                   (automation, "releases", [release_record(draft=True, assets=self.assets)]),
                                   (automation, "tag_commit", COMMIT),
                                   (automation.release, "verify", (VERSION, "d" * 64)),
                                   (automation.subprocess, "run", None)]:
            mock = patch.object(owner, name, return_value=value)
            setattr(self, name, mock.start())
            self.addCleanup(mock.stop)

    def test_failed_evidence_never_creates_a_tag_or_release(self):
        self.verify.side_effect = ValueError("Integration failed")
        with self.assertRaisesRegex(ValueError, "Integration failed"):
            automation.stage(self.directory)
        self.run.assert_not_called()

    def test_published_release_never_mutates(self):
        self.releases.return_value = [release_record(assets=self.assets)]
        with self.assertRaisesRegex(ValueError, "immutable"):
            automation.stage(self.directory)
        self.run.assert_not_called()

    def test_complete_draft_does_not_upload_or_rebuild(self):
        automation.stage(self.directory)
        commands = [call.args[0] for call in self.run.call_args_list]
        self.assertEqual(commands, [["git", "fetch", "origin", f"refs/tags/{VERSION}:refs/tags/{VERSION}"]])

    def test_partial_upload_adds_only_missing_files(self):
        self.releases.return_value = [release_record(draft=True, assets=self.assets[:1])]
        automation.stage(self.directory)
        uploads = [call.args[0] for call in self.run.call_args_list if call.args[0][:3] == ["gh", "release", "upload"]]
        self.assertEqual(len(uploads), 1)
        self.assertNotIn(str(self.directory / self.assets[0]["name"]), uploads[0])
        for asset in self.assets[1:]:
            self.assertIn(str(self.directory / asset["name"]), uploads[0])
        self.assertNotIn("--clobber", uploads[0])

    def test_different_remote_bytes_are_not_overwritten(self):
        self.assets[0]["digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ValueError, "refusing overwrite"):
            automation.stage(self.directory)
        self.assertFalse(any(call.args[0][0] == "gh" for call in self.run.call_args_list))

    def test_new_tag_uses_tested_commit_and_draft_precedes_assets(self):
        self.releases.side_effect = [[], [release_record(draft=True, assets=[])]]
        self.tag_commit.side_effect = [None, COMMIT]
        automation.stage(self.directory)
        calls = self.run.call_args_list
        payload = json.loads(calls[0].kwargs["input"])
        self.assertEqual(payload, {"ref": f"refs/tags/{VERSION}", "sha": COMMIT})
        commands = [call.args[0] for call in calls]
        create = next(i for i, command in enumerate(commands) if command[:3] == ["gh", "release", "create"])
        upload = next(i for i, command in enumerate(commands) if command[:3] == ["gh", "release", "upload"])
        self.assertLess(create, upload)
        self.assertIn("--draft", commands[create])
        self.assertIn("--verify-tag", commands[create])

    def test_changed_remote_tag_stops_before_release_creation(self):
        self.releases.return_value = []
        self.tag_commit.side_effect = [COMMIT, "c" * 40]
        with self.assertRaisesRegex(ValueError, "Remote tag differs"):
            automation.stage(self.directory)
        self.assertFalse(any(call.args[0][0] == "gh" for call in self.run.call_args_list))


if __name__ == "__main__":
    unittest.main(verbosity=2)
