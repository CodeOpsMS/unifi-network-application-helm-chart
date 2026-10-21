#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
# shellcheck source=/dev/null
source .tools/env.sh
export PATH="$PWD/.tools/ci:$PATH"
export KUBECONFIG="$PWD/.tools/ci/kubeconfig"
state=build/ci-kind/cluster-name

cleanup() {
  if [[ -f "$state" ]]; then
    local name
    name="$(cat "$state")"
    [[ "$name" =~ ^unifi-ci-[0-9]+-[0-9]+$ ]] || return 1
    kind delete cluster --name "$name"
    rm -f "$state" "$KUBECONFIG"
  fi
}

if [[ "${1:-}" == cleanup ]]; then
  cleanup
  exit 0
fi
[[ $# == 1 && -f "$1" ]] || {
  echo 'Usage: kind-smoke.sh PACKAGE | cleanup' >&2
  exit 1
}
[[ "${GITHUB_RUN_ID:-}" =~ ^[0-9]+$ && "${GITHUB_RUN_ATTEMPT:-}" =~ ^[0-9]+$ ]] || {
  echo 'Run this fixture in GitHub Actions with a unique run ID and attempt.' >&2
  exit 1
}
[[ ! -e "$state" ]] || {
  echo 'A previous CI cluster needs cleanup first.' >&2
  exit 1
}
python3 scripts/integration/ci-tools.py
docker info >/dev/null
mkdir -p build/ci-kind
ci_cluster_name="unifi-ci-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
if kind get clusters | grep -Fxq "$ci_cluster_name"; then
  echo 'Refusing to reuse an existing cluster.' >&2
  exit 1
fi
printf '%s\n' "$ci_cluster_name" >"$state"
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
node_image="$(python3 -c 'import json; print(json.load(open("scripts/integration/ci-tools.lock.json"))["nodeImage"])')"
kind create cluster --name "$ci_cluster_name" --image "$node_image" --wait 3m
context="kind-${ci_cluster_name}"
kube=(kubectl --context "$context" --request-timeout=30s)
"${kube[@]}" apply -f .tools/ci/metrics.yaml
"${kube[@]}" apply -f .tools/ci/ingress.yaml
"${kube[@]}" rollout status deployment/metrics-server -n kube-system --timeout=5m
"${kube[@]}" rollout status deployment/ingress-nginx-controller -n ingress-nginx --timeout=5m
for ((attempt = 0; attempt < 30; attempt++)); do
  if "${kube[@]}" top node "${ci_cluster_name}-control-plane"; then
    break
  fi
  sleep 5
done
python3 -u scripts/integration/suseai-smoke.py \
  --package "$1" --context "$context" --worker "${ci_cluster_name}-control-plane" \
  --storage-class standard --ingress-namespace ingress-nginx \
  --ingress-selector 'app.kubernetes.io/name=ingress-nginx,app.kubernetes.io/component=controller' \
  --evidence build/ci-integration
