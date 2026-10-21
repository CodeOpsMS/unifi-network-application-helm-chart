#!/usr/bin/env python3
"""Fetch checksum-pinned tools and fixtures for an isolated Linux/AMD64 CI cluster."""

import hashlib
import json
from pathlib import Path
import platform
import subprocess

import yaml


def main():
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "amd64"}:
        raise RuntimeError("The CI fixture requires a Linux/AMD64 Docker host")
    lock = json.loads(Path(__file__).with_name("ci-tools.lock.json").read_text())
    output = Path(".tools/ci")
    output.mkdir(parents=True, exist_ok=True)
    for name, entry in lock["downloads"].items():
        path = output / name
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            subprocess.run(["curl", "--fail", "--location", "--silent", "--show-error", "--retry", "3",
                            entry["url"], "--output", str(path)], check=True)
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise RuntimeError("Checksum mismatch: " + name)
        if entry.get("executable"):
            path.chmod(0o755)
    metrics = list(yaml.safe_load_all((output / "metrics-source.yaml").read_text()))
    deployment = next(obj for obj in metrics if obj["kind"] == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    container["image"] = lock["metricsImage"]
    # kind's disposable kubelet has a self-signed serving certificate. This option
    # is confined to the metrics-server fixture, never an external kubeconfig.
    container["args"].append("--kubelet-insecure-tls")
    (output / "metrics.yaml").write_text(yaml.safe_dump_all(metrics))


if __name__ == "__main__":
    main()
