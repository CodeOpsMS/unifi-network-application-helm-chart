#!/usr/bin/env python3
"""Create an existing-Secret input without putting credentials in arguments or files.

Create the MongoDB user with its original, unencoded credentials first. This
helper stores URI-percent-encoded credentials for the LinuxServer image only.
It intentionally creates a new Secret and does not overwrite an existing one.
"""

import argparse
import base64
import getpass
import json
import subprocess
import sys
import warnings
from urllib.parse import quote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--name", default="unifi-database")
    args = parser.parse_args()
    if not sys.stdin.isatty():
        parser.error("Run interactively; credentials must not be piped from a file.")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            username = getpass.getpass("MongoDB application username (original, not URI-encoded): ")
            password = getpass.getpass("MongoDB application password (original, not URI-encoded): ")
            confirm = getpass.getpass("Confirm application password: ")
    except (getpass.GetPassWarning, EOFError, KeyboardInterrupt):
        sys.exit("Secure credential input was unavailable or cancelled; no Secret was submitted.")
    if not username or not password:
        parser.error("Username and password must both be nonempty.")
    if password != confirm:
        parser.error("Passwords do not match.")

    def encode(value):
        return base64.b64encode(quote(value, safe="").encode("utf-8")).decode("ascii")

    manifest = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": args.name, "namespace": args.namespace},
        "type": "Opaque",
        "data": {"username": encode(username), "password": encode(password)},
    }
    try:
        result = subprocess.run(
            ["kubectl", "--context", args.context, "create", "-f", "-"],
            input=json.dumps(manifest),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError:
        sys.exit("Could not start kubectl. Check its installation; no Secret was submitted.")
    if result.returncode:
        sys.exit(
            "Secret creation failed. Check context, namespace, permissions, and whether "
            "the name already exists. Raw kubectl output is suppressed to avoid "
            "accidentally displaying credentials."
        )
    print(f"Created Secret {args.namespace}/{args.name} in context {args.context}.")


if __name__ == "__main__":
    main()
