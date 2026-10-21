# Contributing

Open an issue describing the intended behavior, or submit a focused pull request with the reason for the change and relevant validation. Keep database lifecycle management separate from this application chart.

Update `values.yaml`, `values.schema.json`, examples, documentation, and tests together when changing the values API. Preserve the one-replica model, root initialization required by the selected image, existing-Secret credential handling, and persistent storage safeguards unless a proposed change explicitly revises and verifies the operating model.

Use a disposable cluster or the opt-in integration script for runtime checks. Never point a test at production controller storage or restore production backups as part of CI. Remove credentials, controller backups, certificates, and private addresses from submitted artifacts.

Before submitting, run `make bootstrap` followed by `make validate` to execute the repository's checks with the pinned tools and both Helm major versions. `make package` writes the release archive to `build/packages`. For a release, also run `make smoke` against that archive and record the environment and limits. State which checks ran and which remain outstanding; rendering and server dry-run do not prove application startup, database authentication, persistence after restart, or device adoption.

Increment the chart version for a new release, document changes in `CHANGELOG.md`, and keep the application version and pinned image aligned. The release workflow publishes packaged charts to GitHub Pages and GHCR; publication runs automatically after a reviewed version change reaches `main`.

## Release procedure

A push to `main` (including a merged pull request) starts [Publish tested chart](.github/workflows/release.yml). Change `version` in `charts/unifi-network-application/Chart.yaml` in the pull request when changing packaged chart files. The pipeline uses that stable `MAJOR.MINOR.PATCH` version; it does not guess or commit a version bump. A merge that leaves the published chart unchanged skips publication. Changed chart files without a version bump fail clearly, and a new version must be greater than existing published versions.

The automatic workflow performs these steps:

1. Inspect the chart version, existing tags and releases using read-only permissions.
2. Run the complete Helm 3/4 validation and retain the exact Helm 4 archive tested by the render and package checks.
3. Create a disposable Kubernetes cluster with kind on an Ubuntu 24.04 AMD64 GitHub runner. Run all 13 integration checks, including local setup, authentication, persistence, database error handling and ingress TLS. Remove the owned namespace, volumes and cluster.
4. Verify that the static report, integration report, source commit and package hash agree. Only after success, use a separate publishing job to create the version tag and draft release with the chart archive, `summary.json` and `validation.json`.
5. Publish those exact bytes to GHCR and GitHub Pages, verify the Pages download, then make the GitHub release public. The publishing job never rebuilds the archive.

Only the publishing job has repository/package/Pages write permissions and uses the existing `github-pages` environment. The environment must allow deployments from `main`. Tests use an isolated local Docker network on the hosted runner; they need no Rancher login, private-cluster access, repository secrets or self-hosted runner. GitHub's repository `GITHUB_TOKEN` is sufficient. Tag creation and publication happen in the same workflow, so they do not depend on another workflow being triggered by a bot-created tag.

The [CI fixture lock](scripts/integration/ci-tools.lock.json) pins kind, Kubernetes, kubectl, metrics-server and the ingress manifest with checksums or image digests. The pinned ingress-nginx controller is a disposable compatibility fixture for the chart's existing nginx annotations; its [upstream retirement](https://kubernetes.io/blog/2026/01/29/ingress-nginx-statement/) remains relevant when choosing production ingress infrastructure. Metrics-server uses the self-signed kubelet certificate option only inside this fixture. The external SUSE-AI integration command remains available with its original defaults.

Do not create tags or releases manually for the normal merge workflow. Keep published versions immutable. A documentation-only merge after a release does not change that release's source, archive or reports. Static development checks may run with uncommitted changes, but release evidence must identify a clean committed source. Optimized Python execution is rejected for integration checks.

### Checking pipeline changes before merging

The existing **Lint and test** workflow can run the same release tests on a feature branch without publishing anything:

```sh
gh workflow run lint-test.yml \
  --repo CodeOpsMS/unifi-network-application-helm-chart \
  --ref YOUR_BRANCH --field integration=true
```

It uploads `release-inputs` and `release-test-evidence` artifacts. The release workflow itself publishes only from `main`.

For local static validation, run `make bootstrap` and `make validate`. To retain the archive that was actually tested, use:

```sh
source .tools/env.sh
python3 scripts/release-automation.py package
```

This writes the chart archive and static report into `build/release-input/`. To run the existing external-cluster checks, pass that archive to `make smoke` with an explicitly selected context, worker, storage class and evidence directory. The integration runner uses its private copy throughout. Its summary must report `passed: true`, `cleanupPassed: true`, the same source and package hash. A successful fresh test installation does not certify an existing controller migration or device adoption.

### Retrying a release

Use **Re-run failed jobs** on the original workflow run after resolving a transient failure. This preserves its exact commit and previously uploaded test artifact. A complete draft can also resume in a rerun of the whole original workflow: the pipeline downloads its existing archive and reports and verifies them again. It never silently repackages or replaces draft assets. A partial draft requires retrying the failed publishing job with its original artifact; a full rerun stops and explains this instead of mixing evidence from different archives. Tags pointing at a different commit and published releases with missing assets require investigation.

For a new attempt at the current `main` version, **Run workflow** is also available:

```sh
gh workflow run release.yml \
  --repo CodeOpsMS/unifi-network-application-helm-chart --ref main
```

There is no version input: the checkout's `Chart.yaml` determines the version. If `main` has moved since a failed release, rerun the original run instead. Never replace a published package with different bytes. Release assets and CI artifacts retain the evidence used by the publishing job.

## Initial setup and browser review

Use [post-setup-ui.py](scripts/integration/post-setup-ui.py) for an additional interactive review of the existing chart archive. Choose a disposable test environment and run after `make bootstrap`:

```sh
source .tools/env.sh
python3 scripts/integration/post-setup-ui.py \
  --package build/packages/unifi-network-application-1.0.3.tgz \
  --context YOUR_CONTEXT --worker YOUR_TEST_WORKER --storage-class YOUR_STORAGE_CLASS \
  --manual-setup --ui-timeout 1800 \
  --evidence build/post-setup-ui \
  --ready-file /private/tmp/unifi-post-setup-ready.json
```

The ready-file path must be absolute, outside the repository, and unused. The runner reserves it with `phase: "preparing"` and changes it to `phase: "ready"` when the browser handoff is available. The owner-only file points to a separate private `credentialsFile`, the loopback UI URL, and the `completionFile`. Read credentials only for the authorized browser session; never paste them, the handoff file, or screenshots containing them into public reports or artifacts. With `--manual-setup`, those credentials are the planned local account to create in the browser wizard. The interactive window is at most 30 minutes after handoff.

The browser operator completes local initial setup without a cloud account, Wi-Fi creation, or device adoption, then checks login, dashboard, empty device/client lists, Wi-Fi and network settings pages, and the local administrator. Save a harmless controller-name change, log out and back in, and verify the changed name. Restart only this run's application pod after verifying the context and namespace from the handoff; the runner restores forwarding on the same loopback port. Verify the UI, saved name, and local login after restart.

Only after performing every check, write this exact completion structure to the handoff's `completionFile`, using its actual `runId` in place of `RUN_ID` and owner-only file permissions. Write to a sibling temporary file and rename it atomically to `completionFile`, so the runner cannot read partially written JSON:

```json
{
  "runId": "RUN_ID",
  "passed": true,
  "checks": [
    "initial-setup", "local-login", "dashboard", "devices-empty",
    "clients-empty", "wifi-settings", "network-settings", "local-admin",
    "controller-name-save", "logout-login", "post-restart-ui"
  ]
}
```

`passed` must be the JSON boolean `true`; the run identity and all 11 check names are mandatory. Report a failed review with `{"runId":"RUN_ID","passed":false,"checks":[]}` instead of acknowledging checks that did not pass. The runner verifies package provenance against its version tag or the current source before creating cluster resources. After the browser review it verifies persisted configuration, local administrator authentication, site and volume identity and the absence of adopted devices. It attempts automatic namespace/PV and private-file cleanup on completion, failure, or timeout. Review the resulting `build/post-setup-ui/<run-id>/summary.json`; success requires both `passed: true` and `cleanupPassed: true`. This additional UI test does not modify the published chart archive or the existing production controller.
