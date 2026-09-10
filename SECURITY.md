# Security

Report chart vulnerabilities privately through [GitHub private vulnerability reporting](https://github.com/CodeOpsMS/unifi-network-application-helm-chart/security/advisories/new) when enabled. If that channel is unavailable, open an issue requesting a private contact without disclosing exploitation steps, credentials, or private deployment details.

The maintained release line is 1.x. There is no guaranteed response time. Report application or container vulnerabilities to Ubiquiti or LinuxServer as appropriate; this repository maintains the Helm packaging.

Use fixed image versions and review application, base-image, and MongoDB advisories before upgrading. Limit GUI, inform, and database exposure to the networks that need them. Database credentials belong in a separately managed Kubernetes Secret; percent encoding and Kubernetes Secret base64 encoding are not encryption. Protect access to Secrets, `/config`, database backups, and Network backup files.

The image writes connection credentials into `/config/data/system.properties` during first initialization. Secret rotation must also update that persisted file. The image requires root initialization and starts with a self-signed application certificate. TLS terminates at the Ingress or external reverse proxy; disabling upstream certificate verification preserves encryption but not backend identity verification. Consult [the operations guide](docs/OPERATIONS.de.md) for deployment-specific setup.

Do not attach Secrets, unsanitized `system.properties`, backups, private certificates, or authentication-bearing URLs to issues or CI artifacts.
