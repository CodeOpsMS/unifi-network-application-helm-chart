# LinuxServer ls154 / Chart 1.0.5: Imageprüfung

Stand: 17. November 2026. Das neueste [LinuxServer-Release](https://github.com/linuxserver/docker-unifi-network-application/releases/tag/10.6.109-ls154)
ist **10.6.109-ls154**, veröffentlicht am 10.11.2026. Chart **1.0.5** aktualisiert
das bisherige Image **10.6.109-ls153**. Die UniFi-Anwendung bleibt bei **10.6.109**;
`appVersion` bleibt deshalb unverändert.

## Image und Kompatibilität

Der [Quellvergleich ls153 bis ls154](https://github.com/linuxserver/docker-unifi-network-application/compare/10.6.109-ls153...10.6.109-ls154)
enthält genau einen Commit, der ausschließlich `package_versions.txt` verändert.
Beide Dockerfiles, `readme-vars.yml`, die Build-Pipeline und sämtliche Dateien
unter `root/` einschließlich der Startskripte sind unverändert.

Die direkte Registry-Abfrage ergab für `latest` und `10.6.109-ls154` denselben
Index. Die Digests des Index, der Plattformmanifeste und der Konfigurationsblobs
wurden aus den empfangenen Bytes berechnet und gegen ihre Referenzen geprüft.
Die Image-Labels nennen Quellrevision `2219dfa98e6f08507c96bd2742130fd137ea00c9`
und Buildzeit `2026-11-10T22:25:27+00:00`.

| Manifest | SHA256-Digest |
| --- | --- |
| Index | `sha256:f782b39e005d4c104b2cfa3185594ce93b3c9bd4b0622cc6559d417844d2d037` |
| Linux/amd64 | `sha256:f392e2aefb7306bd679fc7c9ac02f59439f6e640cd2b991386c6e907242a79d3` |
| Linux/arm64 | `sha256:028cb3c575106a83bef2fc7f5835b04ef346e368231ac5746432fcaaf3f5a7a2` |

Auf AMD64 und ARM64 ist die OCI-Container-Konfiguration abgesehen von den
Image-Labels identisch zu ls153. Entrypoint `/init`, Arbeitsverzeichnis,
Umgebungsvariablen, Volume `/config` und deklarierte Ports bleiben gleich.
Der Chart behält deshalb seine Values, Templates, externe MongoDB-Anbindung,
PUID/PGID, Java-Heap-Vorgaben, Probes und Speicherverwaltung bei.

## Paket- und Sicherheitsänderungen

Die [Upstream-Paketliste](https://github.com/linuxserver/docker-unifi-network-application/blob/10.6.109-ls154/package_versions.txt)
enthält folgende Änderungen gegenüber ls153:

| Paket | ls153 | ls154 |
| --- | --- | --- |
| `ca-certificates` | `20260816ubuntu0.26.04.1` | `20260816ubuntu0.26.04.2` |
| `ca-certificates-java` | `20260311` | `20260311ubuntu0.26.04.2` |
| `openjdk-25-jre-headless` | `25.0.4.1+1-1~26.04.4` | `25.0.5.0.3+1-1~26.04.1` |
| `jrt-fs` | `25.0.4.1` | `25.0.5.0.3` |
| `libpcre2-8-0` | `10.46-1build1` | `10.46-1ubuntu0.1` |

Die neuen OpenJDK- und PCRE2-Versionen entsprechen den korrigierten Versionen
für Ubuntu 26.04 in [USN-9004-1](https://ubuntu.com/security/notices/USN-9004-1)
und [USN-8974-1](https://ubuntu.com/security/notices/USN-8974-1).
[USN-9001-1](https://ubuntu.com/security/notices/USN-9001-1) dokumentiert die neue
`ca-certificates`-Version und eine Korrektur der Synchronisation des Java-Keystores
mit dem System-Zertifikatsbestand. Die im Image enthaltene Java-Zertifikatspaket-
Revision `.2` ist neuer als die dort genannte korrigierte Revision `.1`.
`artifacthub.io/containsSecurityUpdates` bleibt daher `true`; die Änderungsnotizen
beziehen sich auf diese neuen Pakete. Diese Zuordnung ersetzt keinen vollständigen
Schwachstellenscan. Die UniFi-Anwendung und die MongoDB-Anbindung ändern sich nicht.

## Prüfung und Veröffentlichung

Values, Artifact-Hub-Metadaten und erwartete Runtime-Digests sind auf ls154
abgestimmt. Die bestehende Versionsprüfung bleibt bei **10.6.109**. Frühere
Runtime-Berichte für ls153 werden nicht als Nachweis für dieses Update verwendet.

```sh
make bootstrap
make validate
source .tools/env.sh
python3 scripts/release-automation.py package
```

Das statisch geprüfte Archiv liegt unter
`build/release-input/unifi-network-application-1.0.5.tgz`. Der **Lint and test**-
Workflow kann den sauberen PR-Commit mit `integration=true` zusätzlich in einem
kurzlebigen kind-Cluster prüfen. Die Ergebnisse und das genaue Archiv stehen
in dessen CI-Artefakten; diese Dokumentation nimmt das Ergebnis nicht vorweg.

Nach dem Merge nach `main` testet die vorhandene Release-Pipeline den endgültigen
Commit und veröffentlicht genau dessen geprüftes Archiv als GitHub Release,
auf GitHub Pages und in GHCR. Bis dahin bleibt **1.0.4** die veröffentlichte
Chart-Version. Eine frische Testinstallation zertifiziert keine Migration eines
bestehenden Controllers oder Geräteadoption.

Siehe [Release-Verfahren](../CONTRIBUTING.md#release-procedure) und
[Upgrade-Hinweise](OPERATIONS.de.md#upgrade-auf-chart-105--linuxserver-ls154).
