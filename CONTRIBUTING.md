# Contributing

Open an issue describing the intended behavior, or submit a focused pull request with the reason for the change and relevant validation. Keep database lifecycle management separate from this application chart.

Update `values.yaml`, `values.schema.json`, examples, documentation, and tests together when changing the values API. Preserve the one-replica model, root initialization required by the selected image, existing-Secret credential handling, and persistent storage safeguards unless a proposed change explicitly revises and verifies the operating model.

Use a disposable cluster or the opt-in integration script for runtime checks. Never point a test at production controller storage or restore production backups as part of CI. Remove credentials, controller backups, certificates, and private addresses from submitted artifacts.

Before submitting, run `make bootstrap` followed by `make validate` to execute the repository's checks with the pinned tools and both Helm major versions. `make package` writes the release archive to `build/packages`. For a release, also run `make smoke` against that archive and record the environment and limits. State which checks ran and which remain outstanding; rendering and server dry-run do not prove application startup, database authentication, persistence after restart, or device adoption.

Increment the chart version for a new release, document changes in `CHANGELOG.md`, and keep the application version and pinned image aligned. The release workflow publishes packaged charts to GitHub Pages and GHCR; publication is performed by maintainers from a reviewed release revision.

## Release procedure

Finish and commit the intended source before creating release evidence. The static validation report, integration report, release tag, and publishing checkout must identify the same commit. Do not change tracked files during testing. After any source correction, commit the correction and repeat the relevant release checks with a newly packaged archive.

For version 1.0.0:

```sh
make bootstrap
make validate
make package
make smoke \
  PACKAGE=build/packages/unifi-network-application-1.0.0.tgz \
  SMOKE_ARGS='--context suseai --worker laemk8saiworker2 --storage-class harvester --evidence build/integration'
```

The smoke command creates resources in an owned temporary namespace. Choose the intended context, worker, and storage class before executing it. Keep the exact successful package; do not regenerate it for upload. The static report is `build/validation/validation.json`. The integration run writes `summary.json` under `build/integration/<run-id>/`; inspect its `passed`, `cleanupPassed`, `packageSha256`, `sourceCommit`, and check results.

Use the downloaded tool environment and check the release inputs locally, substituting the actual integration run directory:

```sh
source .tools/env.sh
python3 scripts/release.py verify \
  --package build/packages/unifi-network-application-1.0.0.tgz \
  --summary build/integration/RUN_ID/summary.json \
  --validation build/validation/validation.json
```

Create and push the `1.0.0` tag at that tested commit, then create a **draft** GitHub release for the existing tag. Upload the same archive plus the two reports under these exact asset names:

- `unifi-network-application-1.0.0.tgz`
- `summary.json`
- `validation.json`

Review the draft's notes and assets. Dispatch **Publish tested chart** in GitHub Actions with `version=1.0.0`, or use:

```sh
gh workflow run release.yml \
  --repo CodeOpsMS/unifi-network-application-helm-chart \
  --ref main --field version=1.0.0
```

The workflow checks out the version tag and downloads the draft assets. `scripts/release.py` verifies the source commit, successful checks, package hash, and package/source agreement before publishing the supplied package. It does not run `helm package` again. Configure GitHub Pages publishing and repository/package permissions before the first release, and verify the resulting Helm repository and OCI downloads after the publishing workflow completes.

Keep release versions immutable. If a publishing run stops after a partial upload, inspect its logs and the existing remote artifacts before retrying; do not replace a published package with different bytes. Release reports are available from the [1.0.0 release page](https://github.com/CodeOpsMS/unifi-network-application-helm-chart/releases/tag/1.0.0) once that release is published.
