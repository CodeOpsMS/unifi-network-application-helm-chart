#!/usr/bin/env python3
"""Publish the exact integration-tested archive, never rebuild it for release."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

import yaml

REPOSITORY = "CodeOpsMS/unifi-network-application-helm-chart"
CHART = "unifi-network-application"
OCI = "oci://ghcr.io/codeopsms/helm-charts"
PAGES = "https://codeopsms.github.io/unifi-network-application-helm-chart"
REQUIRED = {
    "worker-ready-and-avx", "mongo-authentication-and-special-character-credentials",
    "helm-and-kubernetes-server-dry-runs", "unifi-10.6.101-setup-status-and-runtime-digests",
    "local-setup-completed-without-cloud-or-devices",
    "unifi-restart-persistence", "mongo-restart-persistence", "same-version-package-upgrade-persistence",
    "negative-missing-key", "negative-bad-password", "negative-unreachable-db",
    "ingress-host-routing-trusted-test-certificate-and-https-backend",
}


def run(cmd, **kwargs):
    return subprocess.check_output(cmd, text=True, **kwargs).strip()


def verify(package, summary, validation):
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    report = json.loads(summary.read_text())
    assert report["passed"] and report["cleanupPassed"], "Integration/cleanup did not pass"
    assert report["packageSha256"] == digest, "Package differs from tested bytes"
    assert REQUIRED <= {c["name"] for c in report["checks"] if c["passed"]}, "Required integration evidence missing"
    assert report["sourceCommit"] == run(["git", "rev-parse", "HEAD"]), "Checkout is not tested commit"
    assert not run(["git", "status", "--porcelain", "--untracked-files=no"]), "Tracked source is dirty"
    lint = json.loads(validation.read_text())
    assert lint["passed"], "Static validation did not pass"
    assert lint["sourceCommit"] == report["sourceCommit"], "Static checks tested another commit"
    assert {"3.21.3", "4.2.4"} <= set(lint["helmVersions"]), "Helm compatibility evidence missing"
    meta = yaml.safe_load(Path(f"charts/{CHART}/Chart.yaml").read_text())
    assert package.name == f"{CHART}-{meta['version']}.tgz"
    assert re.fullmatch(r"\d+\.\d+\.\d+", str(meta["version"]))
    # Helm normalizes Chart.yaml; every other packaged source file must match.
    with tarfile.open(package) as archive:
        expected = {str(Path(CHART) / p.relative_to(Path("charts") / CHART))
                    for p in (Path("charts") / CHART).rglob("*")
                    if p.is_file() and not any(part in {"tests", "ci"} for part in p.relative_to(Path("charts") / CHART).parts)}
        assert {m.name for m in archive.getmembers() if m.isfile()} == expected, "Archive is missing or adds source files"
        for member in archive.getmembers():
            if member.isdir():
                continue
            assert member.isfile(), "Unexpected archive member"
            relative = Path(member.name)
            assert relative.parts[0] == CHART and ".." not in relative.parts
            source = Path("charts") / relative
            data = archive.extractfile(member).read()
            if relative.name == "Chart.yaml":
                assert yaml.safe_load(data) == meta
            else:
                assert source.is_file() and source.read_bytes() == data, f"Archive/source mismatch: {source}"
    return str(meta["version"]), digest


def publish(package, summary, validation):
    version, digest = verify(package, summary, validation)
    release = json.loads(run(["gh", "release", "view", version, "--repo", REPOSITORY, "--json", "isDraft,targetCommitish"]))
    assert release["isDraft"], "Published releases are immutable; refusing overwrite"
    assert run(["git", "rev-parse", f"{version}^{{commit}}"] ) == run(["git", "rev-parse", "HEAD"])
    token = os.environ["GH_TOKEN"]
    with tempfile.TemporaryDirectory(prefix="unifi-release-") as tmp:
        tmp = Path(tmp)
        reg = tmp / "registry.json"
        subprocess.run(["helm", "registry", "login", "ghcr.io", "--username", "CodeOpsMS", "--password-stdin", "--registry-config", str(reg)],
                       input=token + "\n", text=True, check=True)
        existing = subprocess.run(["helm", "show", "chart", OCI + "/" + CHART, "--version", version,
                                   "--registry-config", str(reg)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if existing.returncode:
            # Only absence is safe; an authentication/network failure must not permit overwrite.
            assert any(s in existing.stderr.lower() for s in ["not found", "404", "name_unknown", "manifest_unknown"]), existing.stderr
            subprocess.run(["helm", "push", str(package), OCI, "--registry-config", str(reg)], check=True)
        fetched = tmp / "fetched"
        fetched.mkdir()
        subprocess.run(["helm", "pull", OCI + "/" + CHART, "--version", version, "--destination", str(fetched), "--registry-config", str(reg)], check=True)
        assert hashlib.sha256((fetched / package.name).read_bytes()).hexdigest() == digest
        pages = tmp / "pages"
        pages.mkdir()
        remote = "https://github.com/" + REPOSITORY + ".git"
        subprocess.run(["git", "init", "-b", "gh-pages", str(pages)], check=True)
        subprocess.run(["git", "-C", str(pages), "remote", "add", "origin", remote], check=True)
        if run(["git", "ls-remote", "--heads", remote, "gh-pages"]):
            subprocess.run(["git", "-C", str(pages), "fetch", "--depth=1", "origin", "gh-pages"], check=True)
            subprocess.run(["git", "-C", str(pages), "reset", "--hard", "FETCH_HEAD"], check=True)
        if (pages / package.name).exists():
            assert hashlib.sha256((pages / package.name).read_bytes()).hexdigest() == digest, "Pages version already exists with different bytes; refusing overwrite"
        shutil.copyfile(package, pages / package.name)
        args = ["helm", "repo", "index", str(pages), "--url", PAGES]
        if (pages / "index.yaml").exists():
            args += ["--merge", str(pages / "index.yaml")]
        subprocess.run(args, check=True)
        (pages / ".nojekyll").touch()
        (pages / "index.html").write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>UniFi Helm repository</title><h1>UniFi Network Application Helm chart</h1><p><a href="https://github.com/' + REPOSITORY + '">Documentation and source</a></p><p><a href="index.yaml">Helm repository index</a></p></html>\n')
        # Chart annotations are ready for Artifact Hub. Add artifacthub-repo.yml
        # with the assigned repositoryID when the repository is registered there.
        subprocess.run(["git", "-C", str(pages), "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "-C", str(pages), "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
        subprocess.run(["git", "-C", str(pages), "config", "credential.helper", "!gh auth git-credential"], check=True)
        subprocess.run(["git", "-C", str(pages), "add", "."], check=True)
        if subprocess.run(["git", "-C", str(pages), "diff", "--cached", "--quiet"]).returncode:
            subprocess.run(["git", "-C", str(pages), "commit", "-m", f"Publish tested chart {version}"], check=True)
        subprocess.run(["git", "-C", str(pages), "push", "origin", "HEAD:gh-pages"], check=True)
        shutil.copytree(pages, "build/pages-site", ignore=shutil.ignore_patterns(".git"), dirs_exist_ok=True)
    print(f"Prepared tested chart {version} for Pages deployment: SHA256 {digest}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["verify", "publish"])
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--validation", required=True, type=Path)
    args = parser.parse_args()
    if args.action == "verify":
        version, digest = verify(args.package, args.summary, args.validation)
        print(f"Verified chart {version}: SHA256 {digest}")
    else:
        publish(args.package, args.summary, args.validation)


if __name__ == "__main__":
    main()
