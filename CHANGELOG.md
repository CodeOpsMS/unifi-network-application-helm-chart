# Changelog

## 1.0.2 — 2026-09-25 (unreleased)

- Update UniFi Network Application from 10.6.101 to 10.6.106 and the LinuxServer image from `10.6.101-ls145` to `10.6.106-ls147`, pinning its verified AMD64/ARM64 index digest.
- Include OpenJDK 25 and curl security updates from the image package inventory: [USN-8783-1](https://ubuntu.com/security/notices/USN-8783-1) and [USN-8670-3](https://ubuntu.com/security/notices/USN-8670-3). Mark the chart as containing security updates.
- Preserve the values API, external MongoDB configuration, persistent storage, ports, probes and Java heap settings; the upstream Dockerfiles and application startup scripts have not changed.
- Align runtime version/digest assertions and required release evidence with 10.6.106. Add regressions against inconsistent image metadata and evidence from the previous application version.
- Document upstream changes, image verification and explicit image-pin handling during upgrades in the [10.6.106 review](docs/unifi-10.6.106-review.md).

## 1.0.1 — 2026-09-11

- Reject CPU requests above CPU limits, including equivalent millicore and core quantities, and reject generated Service names invalid on supported Kubernetes versions.
- Keep local health checks direct when proxy environment variables are configured. Allow the same NodePort number for TCP and UDP while rejecting duplicates within one protocol.
- Expand render, negative-diagnostic, actual probe-command and Kubernetes 1.25/1.34 schema coverage.
- Bind validation and runtime evidence to the actual committed source and a private immutable test-package copy. Require complete, strictly typed release evidence even under Python optimization.
- Add offline regressions for release gates, source changes, archive identity and test ownership. Verify local UniFi login and a protected API in fresh sessions after setup, restarts and upgrade.
- Add an optional disposable browser-review runner with verified package provenance, bounded forwarding and cleanup.

## 1.0.0

- Initial CodeOpsMS release for the LinuxServer UniFi Network Application image and separately operated MongoDB.
- Single application Deployment with Recreate updates, configurable probes requiring UniFi's `meta.up` status, configurable Java heap, persistent `/config`, and PVC retention.
- Existing-Secret database credentials, explicit database connection settings, and schema validation.
- Mixed TCP/UDP Service with optional portal, speed-test, and syslog ports; optional TLS Ingress with an HTTPS backend.
- Helm checks, an opt-in isolated cluster smoke test, Pages/OCI release workflow, installation examples, and a German operations and migration guide.

Deployment and migration validation are reported per run; this changelog does not certify a particular cluster or a source-controller restore.
