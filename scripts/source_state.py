"""Record the actual working sources used by a test, not just Git HEAD."""

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

ROOT = Path(__file__).resolve().parent.parent


def capture_source(root=None):
    root = Path(root or ROOT).resolve()

    def git(*args):
        result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
        if result.returncode:
            raise RuntimeError("Cannot identify the Git source used for testing")
        return result.stdout

    commit = git("rev-parse", "--verify", "HEAD").decode().strip()
    paths = sorted(set(git("ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0")) - {b""})
    entries = []
    for encoded in paths:
        name = os.fsdecode(encoded)
        path = root / name
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            entries.append([name, "missing"])
            continue
        if stat.S_ISLNK(mode):
            kind, contents = "symlink", os.fsencode(os.readlink(path))
        elif stat.S_ISREG(mode):
            kind, contents = "file", path.read_bytes()
        else:
            raise RuntimeError("Unsupported source file type: " + name)
        entries.append([name, kind, bool(mode & 0o111), hashlib.sha256(contents).hexdigest()])
    fingerprint = hashlib.sha256(json.dumps(entries, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
    return {"commit": commit, "clean": not bool(git("status", "--porcelain", "--untracked-files=all")),
            "fingerprint": fingerprint}


def assert_unchanged(before, root=None, require_clean=False):
    current = capture_source(root)
    if require_clean and (before.get("clean") is not True or current["clean"] is not True):
        raise RuntimeError("Commit source changes before creating release or integration evidence")
    if current != before:
        raise RuntimeError("Source changed during testing; discard this run and repeat against a committed revision")
    return current
