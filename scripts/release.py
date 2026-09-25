#!/usr/bin/env python3
"""Publish the exact integration-tested archive, never rebuild it for release."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

import yaml

from source_state import capture_source

REPOSITORY = "CodeOpsMS/unifi-network-application-helm-chart"
CHART = "unifi-network-application"
OCI = "oci://ghcr.io/codeopsms/helm-charts"
PAGES = "https://codeopsms.github.io/unifi-network-application-helm-chart"
REQUIRED = {
    "worker-ready-and-avx", "mongo-authentication-and-special-character-credentials",
    "helm-and-kubernetes-server-dry-runs", "unifi-10.6.106-setup-status-and-runtime-digests",
    "local-setup-completed-without-cloud-or-devices",
    "local-admin-authentication-and-protected-api",
    "unifi-restart-persistence", "mongo-restart-persistence", "same-version-package-upgrade-persistence",
    "negative-missing-key", "negative-bad-password", "negative-unreachable-db",
    "ingress-host-routing-trusted-test-certificate-and-https-backend",
}
STATIC_CHECKS = {
    "yamlLint", "jsonSchema", "pythonSyntax", "shellcheck", "bashSyntax", "shfmt",
    "actionlint", "gitDiffCheck", "chartTesting", "helmLint", "helmUnitTests",
    "javaPropertiesBehavior", "evidenceBehavior",
}
HELM_VERSIONS = {"3.21.3", "4.2.4"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def package_metadata(package):
    with tarfile.open(package) as archive:
        members = [m for m in archive.getmembers() if m.name == CHART + "/Chart.yaml"]
        require(len(members) == 1 and members[0].isfile(), "Archive must contain one Chart.yaml")
        meta = yaml.safe_load(archive.extractfile(members[0]).read())
    require(isinstance(meta, dict) and meta.get("name") == CHART, "Unexpected chart identity")
    require(re.fullmatch(r"\d+\.\d+\.\d+", str(meta.get("version", ""))), "Invalid chart version")
    return meta


def verify_archive(package, sources):
    meta = yaml.safe_load(sources[CHART + "/Chart.yaml"])
    with tarfile.open(package) as archive:
        members = archive.getmembers()
        files = [m for m in members if m.isfile()]
        require(len({m.name for m in files}) == len(files), "Duplicate archive member")
        require({m.name for m in files} == set(sources), "Archive is missing or adds source files")
        for member in members:
            relative = Path(member.name)
            require(not relative.is_absolute() and relative.parts[0] == CHART and ".." not in relative.parts,
                    "Unsafe archive member")
            if member.isdir():
                continue
            require(member.isfile(), "Unexpected archive member")
            data = archive.extractfile(member).read()
            if member.name == CHART + "/Chart.yaml":
                require(yaml.safe_load(data) == meta, "Archive chart metadata differs from source")
            else:
                require(data == sources[member.name], "Archive/source mismatch: " + member.name)


def chart_sources(ref=None):
    prefix = "charts/" + CHART + "/"
    if ref:
        names = run(["git", "ls-tree", "-r", "--name-only", ref, "--", prefix]).splitlines()
        read = lambda name: subprocess.check_output(["git", "show", ref + ":" + name])
    else:
        names = [str(p) for p in Path(prefix).rglob("*") if p.is_file()]
        read = lambda name: Path(name).read_bytes()
    return {name.removeprefix("charts/"): read(name) for name in names
            if not any(part in {"tests", "ci"} for part in Path(name).relative_to("charts").parts)}


def package_source_commit(package):
    """Attribute a browser fixture only after verifying its bytes against source."""
    meta = package_metadata(package)
    tag = subprocess.run(["git", "rev-parse", "--verify", "refs/tags/" + str(meta["version"]) + "^{commit}"],
                         text=True, capture_output=True, check=False)
    ref = tag.stdout.strip() if tag.returncode == 0 else None
    verify_archive(package, chart_sources(ref))
    return meta, ref or capture_source()["commit"]


def run(cmd, **kwargs):
    return subprocess.check_output(cmd, text=True, **kwargs).strip()


def verify(package, summary, validation):
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    report = json.loads(summary.read_text())
    require(report.get("passed") is True and report.get("cleanupPassed") is True, "Integration/cleanup did not pass")
    require(report.get("packageSha256") == digest, "Package differs from tested bytes")
    checks = report.get("checks")
    require(isinstance(checks, list) and all(isinstance(c, dict) and isinstance(c.get("name"), str)
            and c.get("passed") is True for c in checks), "Invalid integration check results")
    names = [c["name"] for c in checks]
    require(len(names) == len(set(names)) and REQUIRED <= set(names), "Required integration evidence missing")
    state = capture_source()
    require(state["clean"] is True, "Tracked or untracked source is dirty")
    require(report.get("sourceCommit") == state["commit"] and report.get("sourceState") == state,
            "Integration evidence does not identify this clean source")
    lint = json.loads(validation.read_text())
    require(lint.get("passed") is True, "Static validation did not pass")
    require(lint.get("sourceCommit") == state["commit"] and lint.get("sourceState") == state,
            "Static evidence does not identify this clean source")
    require(set(lint.get("helmVersions", [])) == HELM_VERSIONS, "Helm compatibility evidence missing")
    require(isinstance(lint.get("checks"), dict) and all(lint["checks"].get(name) is True for name in STATIC_CHECKS),
            "Static checks are missing or failed")
    spec = importlib.util.spec_from_file_location("chart_validation", Path(__file__).with_name("validate-chart.py"))
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    matrix = lint.get("matrix")
    require(isinstance(matrix, list) and len(matrix) == 2, "Both Helm matrices are required")
    versions = set()
    for entry in matrix:
        require(isinstance(entry, dict) and entry.get("result") == "pass", "A Helm matrix failed")
        version = entry.get("helm", "").lstrip("v").split("+", 1)[0]
        require(version not in versions and version in HELM_VERSIONS, "Unexpected or duplicate Helm matrix")
        versions.add(version)
        require(set(validator.POSITIVE) <= set(entry.get("positive", [])), "Positive render evidence missing")
        require({"empty-defaults", *validator.NEGATIVE} <= set(entry.get("negative", [])), "Negative render evidence missing")
        require(type(entry.get("healthProbeBehaviorCases")) is int and entry["healthProbeBehaviorCases"] >= 90,
                "Health probe behavior evidence missing")
        require({"1.25.16", "1.34.6"} <= set(entry.get("kubernetesSchemaVersions", [])), "Kubernetes schema evidence missing")
        require(entry.get("schemaRevision") == validator.SCHEMA_COMMIT, "Unexpected Kubernetes schema revision")
        schemas = entry.get("schemaCases", {})
        require(isinstance(schemas, dict) and set(validator.POSITIVE) <= set(schemas.get("1.34.6", []))
                and {"default", "custom-routing", "static-pvc", "existing-pvc", "ephemeral-test"}
                <= set(schemas.get("1.25.16", [])), "Kubernetes schema case evidence missing")
        archive = entry.get("package", {})
        require(isinstance(archive, dict) and archive.get("name") == package.name
                and re.fullmatch(r"[a-f0-9]{64}", str(archive.get("sha256", ""))), "Static package evidence missing")
    require(any(entry["package"]["sha256"] == digest for entry in matrix), "Static checks did not test this package")
    meta = package_metadata(package)
    require(package.name == f"{CHART}-{meta['version']}.tgz", "Package filename/version mismatch")
    verify_archive(package, chart_sources())
    return str(meta["version"]), digest


def publish(package, summary, validation):
    version, digest = verify(package, summary, validation)
    release = json.loads(run(["gh", "release", "view", version, "--repo", REPOSITORY, "--json", "isDraft,targetCommitish"]))
    require(release["isDraft"] is True, "Published releases are immutable; refusing overwrite")
    require(run(["git", "rev-parse", f"{version}^{{commit}}"] ) == run(["git", "rev-parse", "HEAD"]), "Release tag is not the tested commit")
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
            require(any(s in existing.stderr.lower() for s in ["not found", "404", "name_unknown", "manifest_unknown"]), existing.stderr)
            subprocess.run(["helm", "push", str(package), OCI, "--registry-config", str(reg)], check=True)
        fetched = tmp / "fetched"
        fetched.mkdir()
        subprocess.run(["helm", "pull", OCI + "/" + CHART, "--version", version, "--destination", str(fetched), "--registry-config", str(reg)], check=True)
        require(hashlib.sha256((fetched / package.name).read_bytes()).hexdigest() == digest, "OCI bytes differ from tested package")
        pages = tmp / "pages"
        pages.mkdir()
        remote = "https://github.com/" + REPOSITORY + ".git"
        subprocess.run(["git", "init", "-b", "gh-pages", str(pages)], check=True)
        subprocess.run(["git", "-C", str(pages), "remote", "add", "origin", remote], check=True)
        if run(["git", "ls-remote", "--heads", remote, "gh-pages"]):
            subprocess.run(["git", "-C", str(pages), "fetch", "--depth=1", "origin", "gh-pages"], check=True)
            subprocess.run(["git", "-C", str(pages), "reset", "--hard", "FETCH_HEAD"], check=True)
        if (pages / package.name).exists():
            require(hashlib.sha256((pages / package.name).read_bytes()).hexdigest() == digest, "Pages version already exists with different bytes; refusing overwrite")
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
