# UniFi Network Application

This chart runs one UniFi Network Application instance using the LinuxServer
image, persistent `/config` storage, and a separately managed MongoDB.

MongoDB must be provisioned and its application user created **before** installing
the chart. This package contains no database deployment, initialization job,
database PVC, or chart dependency.

Set `externalDatabase.host` and `externalDatabase.existingSecret`. The existing
Secret must be in the release namespace and contain URI-encoded `username` and
`password` values. The MongoDB user itself uses the original, unencoded values.
Database names default to `unifi` with `authSource=admin`.

```yaml
externalDatabase:
  host: mongodb.database.svc.cluster.local
  existingSecret: unifi-database

persistence:
  storageClass: your-storage-class
```

The default Java heap is 512–1024 MiB with a 1536 MiB memory request and a 2 GiB
memory limit. Memory values use integer `Mi` or `Gi` quantities. Validation
requires at least 512 MiB between the maximum heap and container memory limit.

Startup, readiness, and liveness probes query HTTPS `/status` and require
`meta.up=true` in its JSON response. UniFi can return HTTP 200 while still
starting. These checks use `curl` and `jq`, verified in the pinned image.

A chart-created 5 GiB PVC is retained on uninstall by default. To reuse storage,
set `persistence.existingClaim`. Namespace deletion still deletes retained PVCs.
Ephemeral storage requires both `persistence.enabled=false` and
`persistence.testOnlyEphemeral=true`.

The default Service is ClusterIP. Device access requires a stable address for
TCP 8080 and UDP 3478 outside the cluster when devices are on the LAN. Optional
HTTPS Ingress exposes management only; NGINX connects to UniFi using HTTPS on
8443 and normally accepts its self-signed certificate. An external NGINX proxy
can also connect to a reachable NodePort or LoadBalancer Service over HTTPS.

The image persists MongoDB connection details in `/config/data/system.properties`
on first initialization. Updating a Secret alone does not rotate these stored
credentials. Follow the maintenance procedure before changing connection details.

See the [installation and configuration guide](https://github.com/CodeOpsMS/unifi-network-application-helm-chart#readme),
[German operations guide](https://github.com/CodeOpsMS/unifi-network-application-helm-chart/blob/main/docs/OPERATIONS.de.md),
and [complete values](https://github.com/CodeOpsMS/unifi-network-application-helm-chart/blob/main/charts/unifi-network-application/values.yaml).
