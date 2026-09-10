# Changelog

## 1.0.0

- Initial CodeOpsMS release for the LinuxServer UniFi Network Application image and separately operated MongoDB.
- Single application Deployment with Recreate updates, configurable probes and Java heap, persistent `/config`, and PVC retention.
- Existing-Secret database credentials, explicit database connection settings, and schema validation.
- Mixed TCP/UDP Service with optional portal, speed-test, and syslog ports; optional TLS Ingress with an HTTPS backend.
- Helm checks, an opt-in isolated cluster smoke test, Pages/OCI release workflow, installation examples, and a German operations and migration guide.

Deployment and migration validation are reported per run; this changelog does not certify a particular cluster or a source-controller restore.
