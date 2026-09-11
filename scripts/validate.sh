#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ ! -f .tools/env.sh ]]; then
  echo 'Run make bootstrap before validation.' >&2
  exit 1
fi
# shellcheck source=/dev/null
source .tools/env.sh
mkdir -p build/validation
# A failed rerun must never leave a previous success report available to release.
rm -f build/validation/validation.json
python3 - <<'PY'
import json
from pathlib import Path
import sys
sys.path.insert(0, 'scripts')
from source_state import capture_source
Path('build/validation/source-state.json').write_text(json.dumps(capture_source()))
PY

yamllint --strict --config-file .yamllint.yaml .
while IFS= read -r -d '' script; do
  bash -n "$script"
  shellcheck "$script"
  shfmt -i 2 -ci -d "$script"
done < <(find scripts -type f -name '*.sh' -print0)
actionlint
git diff --check
python3 - <<'PY'
import json
from pathlib import Path
import jsonschema
for name in ["charts/unifi-network-application/values.schema.json", "scripts/bootstrap-tools.lock.json"]:
    data = json.loads(Path(name).read_text())
    if name.endswith("schema.json"):
        jsonschema.Draft7Validator.check_schema(data)
for path in Path("scripts").rglob("*.py"):
    compile(path.read_bytes(), str(path), "exec")
print("JSON syntax and values schema are valid")
PY

if [[ "${1:-}" == --lint-only ]]; then
  exit 0
fi
if [[ $# -gt 0 ]]; then
  echo 'Usage: scripts/validate.sh [--lint-only]' >&2
  exit 1
fi

python3 scripts/validate-persistence.py
python3 scripts/validate-evidence.py

for major in ${HELM_MAJORS:-3 4}; do
  [[ "$major" == 3 || "$major" == 4 ]]
  (
    export PATH="$PWD/.tools/helm${major}-bin:$PATH"
    helm version --short
    ct lint --config ct.yaml >"build/validation/helm${major}-ct.log" 2>&1 || {
      cat "build/validation/helm${major}-ct.log"
      exit 1
    }
    helm lint charts/unifi-network-application --strict \
      --values charts/unifi-network-application/ci/default-values.yaml
    helm unittest --strict charts/unifi-network-application
    python3 scripts/validate-chart.py --helm helm --output "build/validation/helm${major}"
  )
done
python3 - <<'PY'
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, 'scripts')
from source_state import assert_unchanged
state = json.loads(Path('build/validation/source-state.json').read_text())
assert_unchanged(state)
majors = os.environ.get("HELM_MAJORS", "3 4").split()
reports = [json.loads(Path(f"build/validation/helm{major}/summary.json").read_text()) for major in majors]
version = lambda report: report["helm"].lstrip("v").split("+", 1)[0]
summary = {"passed": True, "sourceCommit": state['commit'], "sourceState": state,
           "helmVersions": [version(report) for report in reports],
           "checks": {"yamlLint": True, "jsonSchema": True, "pythonSyntax": True,
                      "shellcheck": True, "bashSyntax": True, "shfmt": True, "actionlint": True,
                      "gitDiffCheck": True, "chartTesting": True, "helmLint": True, "helmUnitTests": True,
                      "javaPropertiesBehavior": True, "evidenceBehavior": True},
           "matrix": reports}
Path("build/validation/validation.json").write_text(json.dumps(summary, indent=2) + "\n")
PY
echo 'All local validation checks passed.'
