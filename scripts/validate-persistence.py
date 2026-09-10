#!/usr/bin/env python3
"""Regression checks for the integration test's semantic Properties comparison."""
import importlib.util
from pathlib import Path


path = Path(__file__).resolve().parent / "integration/suseai-smoke.py"
spec = importlib.util.spec_from_file_location("unifi_smoke", path)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)
fingerprint = smoke.property_fingerprint


def equal(left, right):
    assert fingerprint(left) == fingerprint(right), "Equivalent Java properties must have the same fingerprint"


def different(left, right):
    left_values, left_hash = fingerprint(left)
    right_values, right_hash = fingerprint(right)
    assert left_values != right_values and left_hash != right_hash, "A changed property value must change its fingerprint"


equal(b"uri=mongodb://db:27017/unifi?tls=false\n",
      br"uri=mongodb\://db\:27017/unifi?tls\=false" + b"\n")
equal(b"# original timestamp\na=1\nb=2\n", b"! new timestamp\nb:2\na 1\n")
equal(b"key=first\nkey=last\n", b"key=last\n")
different(b"key=first\nkey=last\n", b"key=last\nkey=first\n")
different(b"key=value \n", b"key=value\n")
equal(b"uri=mongodb://db/\\\n  unifi\n", b"uri=mongodb://db/unifi\n")
equal(b"name=caf\xe9\n", br"name=caf\u00e9" + b"\n")
different(b"port=27017\n", b"port=27018\n")
print("Java Properties persistence comparison: 8 behavioral checks passed")
