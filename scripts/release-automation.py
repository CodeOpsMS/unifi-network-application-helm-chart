#!/usr/bin/env python3
"""Plan merge releases and stage only the exact archive that passed all checks."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import release
from source_state import assert_unchanged, capture_source

ASSETS = ("summary.json", "validation.json")


def version_key(version):
    release.require(isinstance(version, str) and re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version),
                    "Chart version must be a stable MAJOR.MINOR.PATCH version")
    return tuple(map(int, version.split(".")))


def chart_version():
    meta = release.yaml.safe_load(Path("charts", release.CHART, "Chart.yaml").read_text())
    version_key(meta["version"])
    return meta["version"]


def releases():
    pages = json.loads(release.run(["gh", "api", "--paginate", "--slurp",
                                   f"repos/{release.REPOSITORY}/releases?per_page=100"]))
    return [item for page in pages for item in page]


def tag_commit(version):
    version_key(version)
    result = subprocess.run(["git", "rev-parse", "--verify", f"refs/tags/{version}^{{commit}}"],
                            capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def asset_names(version):
    return {f"{release.CHART}-{version}.tgz", *ASSETS}


def plan():
    version = chart_version()
    current = capture_source()
    release.require(current["clean"], "Release planning requires a clean checkout")
    all_releases = releases()  # Authentication/network errors must never mean "new version".
    existing = next((item for item in all_releases if item["tag_name"] == version), None)
    tag = tag_commit(version)
    if existing and not existing["draft"]:
        release.require(tag is not None, "Published release has no local tag; fetch all tags")
        release.require(asset_names(version) <= {asset["name"] for asset in existing["assets"]},
                        "Published release is incomplete; inspect it before attempting recovery")
        release.require(release.chart_sources(tag) == release.chart_sources(),
                        "Chart changed without a version bump; published versions are immutable")
        return {"version": version, "mode": "skip"}
    if existing:
        release.require(tag == current["commit"], "Draft belongs to another commit; rerun its original workflow")
        release.require(asset_names(version) <= {asset["name"] for asset in existing["assets"]},
                        "Incomplete draft: rerun the failed publish job with its original test artifact")
        return {"version": version, "mode": "resume"}
    release.require(tag is None or tag == current["commit"], "Version tag already identifies another commit")
    published = [item["tag_name"] for item in all_releases
                 if not item["draft"] and re.fullmatch(r"\d+\.\d+\.\d+", item["tag_name"])]
    release.require(all(version_key(version) > version_key(previous) for previous in published),
                    "New chart version must be greater than every published version")
    return {"version": version, "mode": "build"}


def prepare_package(directory):
    report_path = Path("build/validation/validation.json")
    report = json.loads(report_path.read_text())
    release.require(report.get("passed") is True, "Static validation failed")
    assert_unchanged(report["sourceState"], require_clean=True)
    entry = next(item for item in report["matrix"] if item["helm"].startswith("v4."))
    name = f"{release.CHART}-{chart_version()}.tgz"
    release.require(entry["package"]["name"] == name, "Static package version mismatch")
    package = Path("build/validation/helm4/package", name)
    release.verify_archive(package, release.chart_sources())
    release.require(hashlib.sha256(package.read_bytes()).hexdigest() == entry["package"]["sha256"],
                    "Static package bytes changed")
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(package, directory / name)
    shutil.copyfile(report_path, directory / "validation.json")


def collect(directory, evidence):
    summaries = list(evidence.glob("*/summary.json"))
    release.require(len(summaries) == 1, "Expected exactly one integration run")
    package = directory / f"{release.CHART}-{chart_version()}.tgz"
    release.verify(package, summaries[0], directory / "validation.json")
    shutil.copyfile(summaries[0], directory / "summary.json")


def stage(directory):
    version = chart_version()
    package = directory / f"{release.CHART}-{version}.tgz"
    _, digest = release.verify(package, directory / "summary.json", directory / "validation.json")
    commit = capture_source()["commit"]
    existing = next((item for item in releases() if item["tag_name"] == version), None)
    release.require(existing is None or existing["draft"], "Published releases are immutable; refusing overwrite")
    tag = tag_commit(version)
    release.require(tag is None or tag == commit, "Version tag identifies another commit")
    if tag is None:
        payload = json.dumps({"ref": f"refs/tags/{version}", "sha": commit})
        subprocess.run(["gh", "api", "--method", "POST", f"repos/{release.REPOSITORY}/git/refs", "--input", "-"],
                       input=payload, text=True, check=True, stdout=subprocess.DEVNULL)
    # Also detects a remotely moved tag instead of silently overwriting it.
    subprocess.run(["git", "fetch", "origin", f"refs/tags/{version}:refs/tags/{version}"], check=True)
    release.require(tag_commit(version) == commit, "Remote tag differs from the tested commit")
    if existing is None:
        notes = (f"Chart {version}, tested source `{commit}`.\n\n"
                 "Static Helm 3/4 validation and all required isolated Kubernetes integration checks, "
                 "including cleanup, passed. The attached reports identify this exact archive.\n\n"
                 f"Package SHA256: `{digest}`.\n\n"
                 f"Helm repository: {release.PAGES}\n\n"
                 f"OCI: `{release.OCI}/{release.CHART}:{version}`\n")
        with tempfile.TemporaryDirectory(prefix="unifi-release-notes-") as temp:
            path = Path(temp, "notes.md")
            path.write_text(notes)
            subprocess.run(["gh", "release", "create", version, "--repo", release.REPOSITORY,
                            "--verify-tag", "--draft", "--title", f"UniFi Network Application Helm chart {version}",
                            "--generate-notes", "--notes-file", str(path)], check=True)
        existing = next(item for item in releases() if item["tag_name"] == version)
    assets = {asset["name"]: asset for asset in existing["assets"]}
    missing = []
    for name in sorted(asset_names(version)):
        path = directory / name
        expected = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        if name in assets:
            release.require(assets[name].get("digest") == expected, "Draft asset differs; refusing overwrite: " + name)
        else:
            missing.append(str(path))
    if missing:
        subprocess.run(["gh", "release", "upload", version, *missing, "--repo", release.REPOSITORY], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "package", "collect", "stage"])
    parser.add_argument("--directory", type=Path, default=Path("build/release-input"))
    parser.add_argument("--evidence", type=Path, default=Path("build/ci-integration"))
    args = parser.parse_args()
    if args.action == "plan":
        result = plan()
        print(json.dumps(result))
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as output:
                for name, value in result.items():
                    output.write(f"{name}={value}\n")
    elif args.action == "package":
        prepare_package(args.directory)
    elif args.action == "collect":
        collect(args.directory, args.evidence)
    else:
        stage(args.directory)


if __name__ == "__main__":
    main()
