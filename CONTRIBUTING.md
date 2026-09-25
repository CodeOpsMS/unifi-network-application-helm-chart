# Contributing

Open an issue describing the intended behavior, or submit a focused pull request with the reason for the change and relevant validation. Keep database lifecycle management separate from this application chart.

Update `values.yaml`, `values.schema.json`, examples, documentation, and tests together when changing the values API. Preserve the one-replica model, root initialization required by the selected image, existing-Secret credential handling, and persistent storage safeguards unless a proposed change explicitly revises and verifies the operating model.

Use a disposable cluster or the opt-in integration script for runtime checks. Never point a test at production controller storage or restore production backups as part of CI. Remove credentials, controller backups, certificates, and private addresses from submitted artifacts.

Before submitting, run `make bootstrap` followed by `make validate` to execute the repository's checks with the pinned tools and both Helm major versions. `make package` writes the release archive to `build/packages`. For a release, also run `make smoke` against that archive and record the environment and limits. State which checks ran and which remain outstanding; rendering and server dry-run do not prove application startup, database authentication, persistence after restart, or device adoption.

Increment the chart version for a new release, document changes in `CHANGELOG.md`, and keep the application version and pinned image aligned. The release workflow publishes packaged charts to GitHub Pages and GHCR; publication is performed by maintainers from a reviewed release revision.

## Release procedure

Finish and commit the intended source before creating release evidence. The static validation report, integration report, release tag, and publishing checkout must identify the same commit and clean source fingerprint. Static development checks may run with uncommitted changes; those reports are not releasable. Optimized Python execution is rejected for the integration checks. Offline release-gate regression tests also exercise python -O. Do not change tracked files during testing. After any source correction, commit the correction and repeat the relevant release checks with a newly packaged archive.

The commands below illustrate the procedure used for version 1.0.2. For a future release, increment the chart version and substitute that new version throughout; published versions must not be overwritten.

```sh
make bootstrap
make validate
make package
make smoke \
  PACKAGE=build/packages/unifi-network-application-1.0.2.tgz \
  SMOKE_ARGS='--context suseai --worker laemk8saiworker2 --storage-class harvester --evidence build/integration'
```

The smoke command creates resources in an owned temporary namespace. Choose the intended context, worker, and storage class before executing it. The runner copies the package into its private directory and uses that copy throughout the run, including upgrades. Keep the exact successful package; do not regenerate it for upload. The static report is `build/validation/validation.json`. The integration run writes `summary.json` under `build/integration/<run-id>/`; inspect its `passed`, `cleanupPassed`, `packageSha256`, `sourceCommit`, and check results.

Use the downloaded tool environment and check the release inputs locally, substituting the actual integration run directory:

```sh
source .tools/env.sh
python3 scripts/release.py verify \
  --package build/packages/unifi-network-application-1.0.2.tgz \
  --summary build/integration/RUN_ID/summary.json \
  --validation build/validation/validation.json
```

Create and push the `1.0.2` tag at that tested commit, then create a **draft** GitHub release for the existing tag. Upload the same archive plus the two reports under these exact asset names:

- `unifi-network-application-1.0.2.tgz`
- `summary.json`
- `validation.json`

Review the draft's notes and assets. Dispatch **Publish tested chart** in GitHub Actions with `version=1.0.2`, or use:

```sh
gh workflow run release.yml \
  --repo CodeOpsMS/unifi-network-application-helm-chart \
  --ref main --field version=1.0.2
```

The workflow checks out the version tag and downloads the draft assets. `scripts/release.py` verifies the source commit, successful checks, package hash, and package/source agreement before publishing the supplied package. It does not run `helm package` again. Configure GitHub Pages publishing and repository/package permissions before the first release, and verify the resulting Helm repository and OCI downloads after the publishing workflow completes.

Keep release versions immutable. If a publishing run stops after a partial upload, inspect its logs and the existing remote artifacts before retrying; do not replace a published package with different bytes. Existing release reports remain available from the [1.0.0 release page](https://github.com/CodeOpsMS/unifi-network-application-helm-chart/releases/tag/1.0.0); a new version receives its own reports only when published.

## Initial setup and browser review

Use [post-setup-ui.py](scripts/integration/post-setup-ui.py) for an additional interactive review of the existing chart archive. Choose a disposable test environment and run after `make bootstrap`:

```sh
source .tools/env.sh
python3 scripts/integration/post-setup-ui.py \
  --package build/packages/unifi-network-application-1.0.2.tgz \
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
