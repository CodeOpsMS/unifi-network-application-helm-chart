# UniFi 10.6.106 / Chart 1.0.2: Updateprüfung

Stand: 25. September 2026. Das Vorgehen orientiert sich an
[Typemill-PR #30](https://github.com/CodeOpsMS/typemill-helm-chart/pull/30/commits):
Release Notes und Container vergleichen, notwendige Chartänderungen ableiten,
Version und Digest gemeinsam aktualisieren und Prüfergebnisse getrennt von einer
Veröffentlichung dokumentieren.

## Version und Image

| Bestandteil | Bisher | Neu |
| --- | --- | --- |
| Chart | 1.0.1 | 1.0.2 |
| UniFi Network Application | 10.6.101 | 10.6.106 |
| LinuxServer-Tag | 10.6.101-ls145 | 10.6.106-ls147 |
| Plattformen | Linux AMD64, ARM64 | Linux AMD64, ARM64 |

Das [LinuxServer-Release vom 22.09.2026](https://github.com/linuxserver/docker-unifi-network-application/releases/tag/10.6.106-ls147)
ist zum Prüfzeitpunkt das aktuelle Release. Der Tag und `latest` lieferten bei der
direkten Abfrage von `lscr.io` denselben OCI-Index. Die SHA256-Werte wurden aus den
empfangenen Manifestbytes berechnet; auch die Plattformmanifeste und ihre
Konfigurationsblobs wurden gegen die referenzierten Digests geprüft.

| Manifest | Verifizierter SHA256-Digest |
| --- | --- |
| Index | `sha256:5f5e76c95b5bd4becb0cdb1b96ef53a468e75ca0f7a096ca5c24fc30998b382a` |
| Linux/amd64 | `sha256:59b41343c8ef2381181bb10e93c2b4d11501125731ed7ce25b35df46951ac9d2` |
| Linux/arm64 | `sha256:cb60fd4ef2a9549db4bc6f9c43ba7dc0e493182d7bbed9568758b0d16749041b` |

Die Image-Labels nennen Version `10.6.106-ls147`, Buildzeit
`2026-09-22T10:14:05+00:00` und Quellrevision
`93314dca98cea4bb87661ceab66a0df70dffedad`. Der Chart pinnt den Index, damit der
Container-Runtime die passende Architektur auswählen kann.

## Auswirkungen auf den Chart

Die [offiziellen UniFi-Release-Notes](https://community.ui.com/releases/f206c01d-3f73-471b-b4a5-2da48f157ea6)
beschreiben MLO-STR-Mesh-Unterstützung für WiFi-7-APs mit UAP-Firmware ab 8.8,
Stabilitätsverbesserungen sowie Korrekturen bei Clientanzeige, Port-Ereignissen,
Multi-Site-Einstellungen und dem Speichern von Netzwerkkonfigurationen. Das sind
Anwendungsfunktionen; dafür sind keine neuen Helm-Values erforderlich.

Der [LinuxServer-Quellvergleich](https://github.com/linuxserver/docker-unifi-network-application/compare/10.6.101-ls145...10.6.106-ls147)
ändert nur Dokumentation und Paketliste. Beide Dockerfiles und die Startskripte
bleiben bytegleich. Auch die OCI-Konfiguration beider Architekturen behält
Entrypoint `/init`, Arbeitsverzeichnis `/usr/lib/unifi`, Volume `/config`,
Umgebungsvariablen und deklarierte TCP-Ports bei.

Damit bleiben die vorhandenen Chartvorgaben passend: externe MongoDB mit
Secretreferenzen, root-Initialisierung mit PUID/PGID, 512–1024 MiB Heap,
persistentes `/config`, eine Instanz mit `Recreate` und HTTPS-Probes auf `/status`
mit `meta.up=true`. `curl` und `jq` sind weiterhin in der Paketliste enthalten.
Die [LinuxServer-Datenbankanforderungen](https://docs.linuxserver.io/images/docker-unifi-network-application/#setting-up-your-external-database)
ändern sich nicht; der Test verwendet weiterhin separat gepinntes MongoDB 7.0.41.

LinuxServer ergänzt einen Verweis auf die vollständige
[Ubiquiti-Portliste](https://help.ui.com/hc/en-us/articles/218506997-Required-Ports-Reference).
Daraus folgt keine neue Pflicht, weitere Serviceports zu öffnen. Die Entfernung
des alten Migrationsabschnitts ist ebenfalls keine neue Zusage für ein In-place-Upgrade
eines Legacy-Controllers. Die vorhandenen Backup-/Restore-Hinweise bleiben bestehen.

Die Values-API und Templates benötigen keine Änderung. Chart 1.0.2 ist deshalb ein
Patch-Release. Runtime-Versionsprüfung, AMD64-Digest und die versionsgebundene
Release-Prüfung werden gemeinsam aktualisiert. Offline-Regressionsprüfungen
erkennen widersprüchliche Chart-/Image-/Testmetadaten und lehnen einen Prüfbeleg
für die vorherige Anwendungsversion ab.

## Sicherheitsmetadaten

Die [Paketliste des neuen Image-Tags](https://github.com/linuxserver/docker-unifi-network-application/blob/10.6.106-ls147/package_versions.txt)
enthält `openjdk-25-jre-headless` in Version `25.0.4.1+1-1~26.04.4` statt
`25.0.4+7-1~26.04` sowie `curl`/`libcurl4t64` in Version `8.18.0-1ubuntu2.5`
statt `8.18.0-1ubuntu2.4`. Diese Versionen entsprechen den behobenen Paketen in
[Ubuntu USN-8783-1](https://ubuntu.com/security/notices/USN-8783-1) und
[USN-8670-3](https://ubuntu.com/security/notices/USN-8670-3).
Deshalb ist `artifacthub.io/containsSecurityUpdates` auf `true` gesetzt.
Das ist eine Zuordnung der dokumentierten Paketupdates, kein vollständiger
Schwachstellenscan und keine Behauptung eines zusätzlichen UniFi-CVE-Fixes.

## Validierung

Die lokalen Prüfungen werden mit den im Repository gepinnten Werkzeugen ausgeführt:

```sh
make bootstrap
make validate
make package
```

Die lokalen Prüfungen bestanden am 25.09.2026 unter Helm **3.21.3** und **4.2.4**:

| Prüfung | Ergebnis |
| --- | --- |
| Striktes Helm-Linting und chart-testing | Beide Helm-Versionen erfolgreich |
| Helm-Unittests | Je 28/28 Tests in fünf Suites |
| Rendering gültiger Konfigurationen | Je 28/28 Fälle |
| Ablehnung ungültiger Konfigurationen | Je 81/81 Fälle |
| Ausführung der Probe-Befehle mit Fehler-/Erfolgs-Fixtures | Je 90/90 Fälle |
| Kubernetes-Schemas | 1.25.16 und 1.34.6 erfolgreich |
| Java-Properties- und Release-Evidence-Regressionsprüfungen | Erfolgreich; 25 Evidence-Tests inklusive Versionskonsistenz und veralteter Belege |
| YAML, JSON-Schema, Python-/Bash-Syntax, ShellCheck, shfmt, actionlint | Erfolgreich |
| Archivinhalt und Paketgrenzen | Beide Helm-Versionen erfolgreich |
| `git diff --check` | Erfolgreich |

Die maschinenlesbaren Ergebnisse stehen unter `build/validation/validation.json`
und `build/validation/helm{3,4}/summary.json`. Das Entwicklungsarchiv ist
`build/packages/unifi-network-application-1.0.2.tgz`. Die Prüfung erfolgte mit
uncommittierten Änderungen (`sourceState.clean=false`); sie ist daher noch kein
veröffentlichungsfähiger Releasebeleg.

Ein SUSE-AI-Integrationstest und ein tatsächliches Upgrade von 10.6.101 auf
10.6.106 wurden in diesem Arbeitsstand nicht ausgeführt.
Der bestehende Smoke-Runner prüft Neuinstallation, Neustarts und ein Upgrade
derselben Chartversion; er ersetzt keinen Versionswechseltest. Die Berichte des
veröffentlichten Charts 1.0.1 gelten nicht für das neue Image.

Für den Rollout gelten die [Upgrade-Anweisungen](OPERATIONS.de.md#upgrade-auf-chart-102--unifi-106106).
Die Veröffentlichung von 1.0.2 erfordert weiterhin einen sauberen Commit und die
in [CONTRIBUTING.md](../CONTRIBUTING.md#release-procedure) beschriebenen statischen
und Laufzeitbelege für genau dasselbe Archiv.
