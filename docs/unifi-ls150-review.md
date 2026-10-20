# LinuxServer ls150 / Chart 1.0.3: Imageprüfung

Stand: 20. Oktober 2026. Das [aktuelle LinuxServer-Release](https://github.com/linuxserver/docker-unifi-network-application/releases/tag/10.6.106-ls150)
wurde am 13.10.2026 veröffentlicht. Seit dem gepinnten Image `10.6.106-ls147`
erschienen ls148, ls149 und ls150. Die UniFi-Anwendung bleibt bei **10.6.106**;
`appVersion` bleibt deshalb unverändert. Die neue Image-Vorgabe erhält Chartversion
**1.0.3**.

## Image und Kompatibilität

Der [Quellvergleich ls147 bis ls150](https://github.com/linuxserver/docker-unifi-network-application/compare/10.6.106-ls147...10.6.106-ls150)
enthält Paketupdates sowie Änderungen an Upstream-Dokumentation, Beitragshinweisen
und Buildvorlagen. Beide Dockerfiles, `readme-vars.yml` und die Dateien unter
`root/` einschließlich der Startskripte sind unverändert.

Tag `10.6.106-ls150` und `latest` lieferten bei der direkten Registry-Abfrage
denselben Index. Index-, Plattformmanifest- und Konfigurationsblob-Digests wurden
aus den empfangenen Bytes berechnet beziehungsweise gegen die Referenzen geprüft.
Die Image-Labels nennen Quellrevision
`8409f40af5cf8ad97ec8b237f739347a52a66de6` und Buildzeit
`2026-10-13T21:54:10+00:00`.

| Manifest | SHA256-Digest |
| --- | --- |
| Index | `sha256:4d5fc889c2f2c2cecc6b35d4d641780cbedbe90d3a51b8f03283261b137af468` |
| Linux/amd64 | `sha256:18a3a1dd05c3c265311f81452d7cd6cb51927d05864dca07a1a5aeff32cb6c5a` |
| Linux/arm64 | `sha256:12ab419f1b2c296db4cfaf0a404c9d63ad8da1c187bb98185763f4a713b52152` |

Auf beiden Architekturen ist die OCI-Container-Konfiguration abgesehen von den
Image-Labels identisch: Entrypoint `/init`, Arbeitsverzeichnis `/usr/lib/unifi`,
Volume `/config`, Umgebungsvariablen und deklarierte Ports bleiben gleich.
Der Chart behält daher seine Values, Templates, MongoDB-Anbindung, PUID/PGID,
Java-Heap-Vorgaben, Probes und Speicherverwaltung bei. OpenJDK 25, `jq` und die
UniFi-Anwendung wurden laut Paketliste nicht aktualisiert.

## Paket- und Sicherheitsänderungen

Die [Paketliste für ls150](https://github.com/linuxserver/docker-unifi-network-application/blob/10.6.106-ls150/package_versions.txt)
enthält gegenüber ls147 folgende Änderungen:

| Pakete | ls147 | ls150 | Referenz |
| --- | --- | --- | --- |
| curl, libcurl4t64 | 8.18.0-1ubuntu2.5 | 8.18.0-1ubuntu2.7 | [USN-8820-1](https://ubuntu.com/security/notices/USN-8820-1) |
| openssl, libssl3t64, openssl-provider-legacy | 3.5.5-1ubuntu3.5 | 3.5.5-1ubuntu3.7 | [USN-8861-1](https://ubuntu.com/security/notices/USN-8861-1) |
| libsqlite3-0 | 3.46.1-9ubuntu0.2 | 3.46.1-9ubuntu0.3 | [USN-8775-1](https://ubuntu.com/security/notices/USN-8775-1) |
| rust-coreutils | 0.8.0-0ubuntu3 | 0.10.0-1ubuntu2~26.04.1 | Upstream-Paketliste |

Die neuen Paketversionen entsprechen den in den Ubuntu-Hinweisen korrigierten
Versionen. `artifacthub.io/containsSecurityUpdates` bleibt daher `true`; die
Änderungsnotizen nennen die zu diesem Update gehörenden Pakete und Hinweise.
Dies ist eine Zuordnung der veröffentlichten Paketdaten, kein vollständiger
Schwachstellenscan und keine Behauptung einer neuen UniFi-Anwendungsversion.

## Prüfung und Veröffentlichung

Die vorhandenen Tests prüfen unter anderem, dass Values, Artifact-Hub-Metadaten
und Runtime-Image-Erwartungen übereinstimmen. Der Integrationstest erwartet nun
die ls150-Digests; die weiterhin gültige Versionsprüfung bleibt bei 10.6.106.

Lokale Reproduktion:

```sh
make bootstrap
make validate
make package
```

Das Archiv heißt `build/packages/unifi-network-application-1.0.3.tgz`.
Die Ergebnisse für den geprüften PR-Commit stehen im PR beziehungsweise in den
CI-Artefakten unter `build/validation/`. Veröffentlichung erfordert weiterhin
statische und erfolgreiche Cluster-Belege für denselben sauberen Commit und
exakt dasselbe Archiv.

Der vorhandene SUSE-AI-Zugang wurde am 20.10.2026 beim lesenden Vorabcheck mit
HTTP 403 (`system:unauthenticated`) abgewiesen. Es wurden keine Testressourcen
angelegt. Damit liegen für ls150 noch keine neuen Nachweise für Anwendungsstart,
Login, Persistenz oder Ingress-TLS vor. Die erfolgreichen Laufzeitprüfungen von
Chart 1.0.2 gelten für ls147 und werden nicht als ls150-Belege verwendet.
Nach Wiederherstellung des Zugangs lässt sich der Test vom sauberen Commit aus
mit dem statisch geprüften Archiv ausführen:

```sh
make smoke \
  PACKAGE=build/packages/unifi-network-application-1.0.3.tgz \
  SMOKE_ARGS='--context suseai --worker laemk8saiworker2 --storage-class harvester --evidence build/integration'
```

Für das Upgrade gelten die [Betriebshinweise](OPERATIONS.de.md#upgrade-auf-chart-103--linuxserver-ls150).
Dieser PR bereitet das Update vor; Tag und Release-Veröffentlichung folgen dem
[bestehenden Release-Verfahren](../CONTRIBUTING.md#release-procedure).
