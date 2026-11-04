# UniFi 10.6.109 / Chart 1.0.4: Kompatibilitätsprüfung

Stand: 4. November 2026. Das neueste [LinuxServer-Release](https://github.com/linuxserver/docker-unifi-network-application/releases/tag/10.6.109-ls153)
ist **10.6.109-ls153**, veröffentlicht am 03.11.2026. Chart **1.0.4** aktualisiert
UniFi von **10.6.106** auf **10.6.109** und das Image von ls150 auf ls153.

## Anwendung und Container

Die [Hersteller-Release-Notizen](https://community.ui.com/releases/UniFi-Network-Application-10-6-109/772020d5-1426-4015-bbd6-631d28803933)
nennen die Entfernung kompromittierter eMudhra-CA-Zertifikate. Deshalb bleibt
`artifacthub.io/containsSecurityUpdates` auf `true`. Der Chart bleibt beim
LinuxServer-Image für die Network Application und übernimmt dessen aktuelle
Version; andere UniFi-Produkte oder Release-Kanäle sind nicht Gegenstand dieses Updates.

Der [Quellvergleich ls150 bis ls153](https://github.com/linuxserver/docker-unifi-network-application/compare/10.6.106-ls150...10.6.109-ls153)
ändert ausschließlich `Jenkinsfile` und `package_versions.txt`. Dockerfiles,
`readme-vars.yml` und sämtliche Dateien unter `root/` einschließlich der
Startskripte sind unverändert. Die Build-Pipeline ergänzt unter anderem
Wiederholungsversuche bei Registry-Limits; daraus ergeben sich keine neuen
Chart-Values oder Kubernetes-Ressourcen.

Die Registry-Prüfung bestätigt dieselbe OCI-Container-Konfiguration auf AMD64
und ARM64 wie bei ls150, abgesehen von Image-Labels. Entrypoint `/init`,
Arbeitsverzeichnis, Umgebungsvariablen, `/config` und deklarierte Ports bleiben
gleich. Externe MongoDB, PUID/PGID, Java-Heap, Probes und Persistenz können daher
beibehalten werden. Die Laufzeittests prüfen zusätzlich das Verhalten des neuen Images.

## Verifizierte Image-Digests

`latest` und `10.6.109-ls153` lieferten denselben Index. Index-, Plattformmanifest-
und Konfigurationsblob-Digests wurden anhand der empfangenen Bytes geprüft.
Die Image-Labels nennen Quellrevision `0742d9566a5de368f7ff2cd4792a2528b1f54cbe`
und Buildzeit `2026-11-03T22:21:20+00:00`.

| Manifest | SHA256-Digest |
| --- | --- |
| Index | `sha256:3a6a1b86b30070fb2e635e44047801626bc224e7c129c2310f236efaa38c58aa` |
| Linux/amd64 | `sha256:e6e3001d400efe532fb8a19b9c3b2f662bffc27a4cc6400f80e5eb9ecda8e292` |
| Linux/arm64 | `sha256:b3f710b3f1aea486ef9f9bcdb5e7103014f25d4deaef336b942ff6b968a96e3e` |

## Weitere Paketänderungen

Die [Upstream-Paketliste](https://github.com/linuxserver/docker-unifi-network-application/blob/10.6.109-ls153/package_versions.txt)
enthält aktualisierte CA-Zertifikate, curl/libcurl, Brotli, liblzma, NSS,
systemd/udev, Zeitzonendaten und zlib. Auch die erfassten Go-Module und die
Go-Standardbibliothek ändern sich. OpenJDK und die MongoDB-Anbindung bleiben
in diesem Vergleich unverändert. Diese Bestandsaufnahme ist kein vollständiger
Schwachstellenscan; die Sicherheitsannotation bezieht sich auf die vom Hersteller
bestätigte Änderung des Zertifikats-Vertrauensbestands.

## Tests und Veröffentlichung

Values, Artifact-Hub-Metadaten und die erwarteten Runtime-Digests sind auf ls153
abgestimmt. Der Release-Prüfer verlangt nun den erfolgreichen Versionsnachweis
für **10.6.109**; ein Bericht für 10.6.106 wird abgewiesen.

```sh
make bootstrap
make validate
source .tools/env.sh
python3 scripts/release-automation.py package
```

Das geprüfte Archiv liegt unter
`build/release-input/unifi-network-application-1.0.4.tgz`. Der manuell gestartete
**Lint and test**-Workflow mit `integration=true` prüft den Update-Branch zusätzlich
in einem kurzlebigen kind-Cluster und stellt Archiv, `summary.json` und
`validation.json` als CI-Artefakte bereit. Die Ergebnisse stehen am jeweiligen
Workflow-Commit; diese Dokumentation nimmt ihr Ergebnis nicht vorweg.

Der Branch umfasst auch die zuvor vorbereitete Release-Automatik. Nach dem Merge
nach `main` testet sie den endgültigen Merge-Commit und veröffentlicht genau dessen
geprüftes Archiv als GitHub Release, auf GitHub Pages und in GHCR. Bis dahin bleibt
**1.0.3** die veröffentlichte Chart-Version. Frühere Berichte werden nicht als
Nachweis für dieses Update verwendet. Die Tests einer frischen Installation
ersetzen keine Prüfung einer bestehenden Controller-Migration oder Geräteadoption.

Siehe [Release-Verfahren](../CONTRIBUTING.md#release-procedure) und
[Upgrade-Hinweise](OPERATIONS.de.md#upgrade-auf-chart-104--unifi-106109).
