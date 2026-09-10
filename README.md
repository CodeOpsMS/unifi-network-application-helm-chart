# UniFi Network Application Helm chart

Deploy one persistent UniFi Network Application instance on Kubernetes with the LinuxServer image and an existing MongoDB server. The chart manages the application, its Service, an optional `/config` PVC, and an optional TLS Ingress. MongoDB is operated separately.

Maintained by [CodeOpsMS](https://github.com/CodeOpsMS). This is a community chart, not a Ubiquiti or LinuxServer product. Chart version **1.0.0** targets UniFi Network Application **10.6.101** using `lscr.io/linuxserver/unifi-network-application:10.6.101-ls145` with the digest in the chart values. UniFi Network Server and UniFi OS Server are different products; this chart packages the Network Application container.

## Install

Prerequisites:

- Kubernetes 1.25 or later (the chart's declared API baseline), Helm 3 or 4, and a working persistent storage class. Runtime validation is recorded per tested environment.
- A reachable, authenticated MongoDB server with a dedicated application user. The chart does not install a database, database users, or credentials.
- An existing Kubernetes Secret containing URI-percent-encoded database credentials, in the release namespace.
- A stable address reachable from the managed devices for TCP 8080 and UDP 3478. A web Ingress alone does not provide device connectivity.
- An existing TLS Secret and compatible Ingress controller when enabling web Ingress.

Create the MongoDB user and Kubernetes Secret using the [database setup instructions](docs/OPERATIONS.de.md#mongodb-und-zugangsdaten). The example helper prompts for credentials and submits the Secret without putting its contents in Git, Helm values, command arguments, or terminal output:

```sh
kubectl --context YOUR_CONTEXT create namespace unifi
python3 examples/create-database-secret.py \
  --context YOUR_CONTEXT --namespace unifi --name unifi-database
```

Copy [examples/values-loadbalancer.yaml](examples/values-loadbalancer.yaml), set your database hostname, storage class, and allocated device-facing address, then install from the Helm repository:

```sh
helm repo add codeopsms https://codeopsms.github.io/unifi-network-application-helm-chart
helm repo update
helm upgrade --install unifi codeopsms/unifi-network-application \
  --version 1.0.0 --namespace unifi --create-namespace \
  --kube-context YOUR_CONTEXT --values my-values.yaml \
  --wait --timeout 20m
```

The same chart is published as an OCI artifact:

```sh
helm upgrade --install unifi \
  oci://ghcr.io/codeopsms/helm-charts/unifi-network-application \
  --version 1.0.0 --namespace unifi --create-namespace \
  --kube-context YOUR_CONTEXT --values my-values.yaml \
  --wait --timeout 20m
```

Repository source: [CodeOpsMS/unifi-network-application-helm-chart](https://github.com/CodeOpsMS/unifi-network-application-helm-chart). Release channels are populated by the release workflow; an unreleased checkout is installed locally with `helm upgrade --install unifi ./charts/unifi-network-application ...`.

## Configuration

See [values.yaml](charts/unifi-network-application/values.yaml) for every option and [values.schema.json](charts/unifi-network-application/values.schema.json) for validation. Required values deliberately have no working default.

| Value | Default | Purpose |
| --- | --- | --- |
| `externalDatabase.host` | empty, required | MongoDB hostname reachable from the pod |
| `externalDatabase.port` | `27017` | MongoDB TCP port |
| `externalDatabase.database` | `unifi` | Base database name |
| `externalDatabase.authSource` | `admin` | Database in which the MongoDB user exists |
| `externalDatabase.tls` | `false` | Enable MongoDB TLS; trust and server identity must also be valid |
| `externalDatabase.existingSecret` | empty, required | Existing Secret in the release namespace |
| `externalDatabase.usernameKey` / `passwordKey` | `username` / `password` | Keys holding URI-percent-encoded credentials |
| `java.initialHeapMiB` / `maxHeapMiB` | `512` / `1024` | Java heap; keep below the container memory limit |
| `resources.requests.memory` / `limits.memory` | `1536Mi` / `2Gi` | Total process memory, including memory outside the heap |
| `env.PUID` / `env.PGID` | `"1000"` / `"1000"` | Application user/group inside the LinuxServer image |
| `env.TZ` | `Etc/UTC` | Application timezone |
| `extraEnv` | `[]` | Additional string values or `valueFrom.secretKeyRef` objects; chart-managed variables cannot be overridden |
| `persistence.existingClaim` | empty | Use an existing `/config` PVC; the chart then creates no PVC |
| `persistence.storageClass` | empty | Empty uses the cluster default; set explicitly when multiple defaults exist |
| `persistence.retain` | `true` | Keep a chart-created PVC when uninstalling the release |
| `service.type` | `ClusterIP` | Choose `LoadBalancer` or `NodePort` when required for device access |
| `service.loadBalancerIP` | empty | Optional address request, subject to the LoadBalancer provider |
| `ingress.enabled` | `false` | Expose the HTTPS web application through Ingress |
| `ingress.className` | `nginx` | Ingress class; default annotations target ingress-nginx |
| `ingress.host` / `tlsSecretName` | empty | Required when Ingress is enabled |

The Deployment uses **one replica and `Recreate`**. This is not an active/active controller deployment. Startup, readiness, and liveness probes are configurable; each reads HTTPS 8443 `/status` and requires the JSON field `meta.up` to be `true`. UniFi can return HTTP 200 while it is still starting, so the probes check application state as well as successful HTTP access. The default startup allowance is about 15 minutes. Persistent storage is required by default; `persistence.enabled: false` additionally requires `persistence.testOnlyEphemeral: true` and is only for disposable tests.

The LinuxServer image initializes as root and then runs the application under PUID/PGID. The chart preserves that startup contract. Do not add an arbitrary `runAsNonRoot` or read-only root filesystem policy without testing a compatible image. Storage must support the image's ownership initialization; restricted Pod Security policies may reject this deployment.

## Network and TLS

| Endpoint | Default | Use |
| --- | --- | --- |
| HTTPS | TCP 8443 | Web UI and API; upstream uses a self-signed certificate by default |
| Inform | TCP 8080 | Device communication; **not the web UI** |
| STUN | UDP 3478 | Device communication |
| Discovery | UDP 10001 | Discovery; exposing a port does not extend layer-2 broadcasts into Kubernetes |
| Portal, optional | TCP 8880 / 8843 | Guest portal, when enabled |
| Speed test, optional | TCP 6789 | Mobile speed test |
| Syslog, optional | UDP 5514 | Remote syslog |

The Service contains both TCP and UDP. A LoadBalancer must support mixed protocols; validate this with your provider and from the device VLANs. Keep inform externally reachable on **8080**. Arbitrary NodePort translation does not by itself change the port advertised by UniFi. Configure a stable Inform Host in UniFi; do not advertise a pod IP or an unresolvable cluster hostname.

For browser access, use [examples/values-ingress.yaml](examples/values-ingress.yaml) together with your base values. TLS terminates at the Ingress, which connects to the application over HTTPS 8443. The default ingress-nginx annotations account for the application's self-signed upstream certificate. Other controllers require equivalent backend configuration. The Ingress does not configure TCP 8080 or UDP forwarding.

For an existing external NGINX server, [examples/nginx-unifi.conf](examples/nginx-unifi.conf) shows browser TLS termination, an HTTPS upstream, and WebSocket headers. It also does not carry inform or STUN traffic. Both arrangements are explained in the [German operations guide](docs/OPERATIONS.de.md#webzugriff-mit-tls).

## Persistence, rotation, and migration

Back up **both the external database and `/config`**, and download Network `.unf` backups. `/config` includes persistent application settings and keystore data. A retained PVC is not a backup.

Database connection settings are written into `/config/data/system.properties` on the image's initial startup. Changing the Kubernetes Secret or `externalDatabase` values on an existing volume does **not** rewrite that file. Credential rotation therefore needs a coordinated database, Secret, and persisted-file update while the application is stopped. See [rotation and restore](docs/OPERATIONS.de.md#rotation-von-datenbankzugangsdaten).

Move existing controllers using a tested Network backup/restore into fresh application storage and an appropriately configured database. Do not mount an old embedded-MongoDB controller directory as this chart's `/config`. Migration includes changing the Inform Host or DNS and verifying all devices. The planned migration from the existing **9.4.19** controller is a separate operation; this repository does not perform it. No existing device adoption is part of the chart installation or its smoke test.

Follow the [migration procedure](docs/OPERATIONS.de.md#migration-eines-bestehenden-controllers) before production use. A Helm rollback does not reverse a database schema migration.

## Validation and releases

Local chart checks and a separate cluster integration test are maintained in [scripts](scripts). The latter is an opt-in runtime check for a new temporary installation, with separate MongoDB credentials and storage. It does not restore the source controller or migrate its devices.

Install the pinned development tools and run local checks with:

```sh
make bootstrap
make validate
make package
```

The validation entry point runs the Helm 3 and Helm 4 checks without installing the application into a cluster. Packaging writes `build/packages/unifi-network-application-1.0.0.tgz`. Run the separate integration procedure against those exact bytes when runtime verification is needed:

```sh
python3 scripts/integration/suseai-smoke.py \
  --package build/packages/unifi-network-application-1.0.0.tgz \
  --context suseai --worker laemk8saiworker2 --storage-class harvester \
  --evidence build/integration
```

The equivalent wrapper is `make smoke SMOKE_ARGS='--context suseai --worker laemk8saiworker2 --storage-class harvester --evidence build/integration'`. Override `PACKAGE=...` when testing a different archive. The wrapper loads the pinned tools from `make bootstrap`.

Read the script and select the intended cluster before running it: unlike rendering or an API dry-run, it creates temporary cluster resources. Test results apply to the versions and environment recorded by that run. CI status does not establish successful production migration, device reachability from every VLAN, or database recovery.

Release publication requires the final source commit, the exact tested `.tgz`, the successful integration `summary.json` including cleanup, and the static `validation.json`. These files are uploaded to a draft release; a manual dispatch of [release.yml](.github/workflows/release.yml) invokes [scripts/release.py](scripts/release.py) to check their agreement and publish the supplied archive to OCI and the Helm repository **without rebuilding it**. The maintainer procedure is in [CONTRIBUTING.md](CONTRIBUTING.md#release-procedure).

After publication, [release 1.0.0](https://github.com/CodeOpsMS/unifi-network-application-helm-chart/releases/tag/1.0.0) provides the chart and validation report downloads: [integration summary.json](https://github.com/CodeOpsMS/unifi-network-application-helm-chart/releases/download/1.0.0/summary.json) and [static validation.json](https://github.com/CodeOpsMS/unifi-network-application-helm-chart/releases/download/1.0.0/validation.json). These are release outputs, not a claim that a current development checkout has passed validation.

See [CONTRIBUTING.md](CONTRIBUTING.md), [CHANGELOG.md](CHANGELOG.md), and [SECURITY.md](SECURITY.md). License notices are retained in [LICENSE](LICENSE).

## References

- [LinuxServer image and external database requirements](https://docs.linuxserver.io/images/docker-unifi-network-application/)
- [Ubiquiti backups and migration](https://help.ui.com/hc/en-us/articles/360008976393-Backups-and-Migration-in-UniFi)
- [Ubiquiti required ports](https://help.ui.com/hc/en-us/articles/218506997-Required-Ports-Reference)
- [MongoDB user creation](https://www.mongodb.com/docs/manual/reference/method/db.createUser/)
