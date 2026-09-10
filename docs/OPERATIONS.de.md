# Betrieb der UniFi Network Application auf Kubernetes

Dieses Chart betreibt eine UniFi Network Application mit externer MongoDB. Die Datenbank einschließlich Benutzerverwaltung, Datensicherung und Updates wird separat bereitgestellt. Der dokumentierte Ausgangscontroller für den geplanten Umzug läuft auf **9.4.19**. Dessen Backup-Wiederherstellung und Geräteumzug stehen noch aus; eine frische Testinstallation ersetzt diese Prüfung nicht.

## Voraussetzungen und Zuständigkeiten

Vor der Installation müssen folgende Angaben feststehen:

| Bereich | Festzulegen |
| --- | --- |
| Datenbank | Erreichbarer Host/Port, kompatible feste Version, Authentifizierungsdatenbank, Anwendungsbenutzer, TLS-Vertrauen, Backup/Restore-Verfahren |
| Kubernetes | Kontext, Namespace, StorageClass, ausreichend Speicher und aufnahmefähiger Node |
| Gerätezugang | Dauerhafte IP oder DNS-Adresse, Routing aus allen Geräte-VLANs, TCP 8080 und UDP 3478 |
| Webzugang | DNS-Name, TLS-Zertifikat und Ingress oder externer Reverse Proxy |
| Migration | Aktuelle Network-Version, Hosttyp, vollständiges Backup, Geräte-SSH-Zugang, Wartungsfenster, Rückkehrplan |

Der Chartstandard verwendet `ClusterIP`. Damit ist die Anwendung zunächst innerhalb des Clusters erreichbar. Eine Portweiterleitung eignet sich zur Einrichtung, aber nicht als dauerhafte Geräteanbindung. Für produktiven Zugriff kann der Service auf `LoadBalancer` umgestellt werden. Dessen Anbieter muss TCP und UDP in einem Service unterstützen; eine vom Kubernetes-API-Server akzeptierte Konfiguration belegt das noch nicht.

In Umgebungen mit mehreren Standard-StorageClasses wird `persistence.storageClass` ausdrücklich gesetzt. Das Beispiel nutzt `harvester`; dies ist eine Umgebungsentscheidung und kein allgemeiner Chartstandard. Bei extern betriebener MongoDB gilt deren eigener CPU- und Storage-Bedarf. Insbesondere benötigt MongoDB neuer als 4.4 auf x86-64 AVX. Eine Datenbankversion wird unabhängig vom Anwendungsimage festgeschrieben und nach dem jeweiligen MongoDB-Upgradeverfahren aktualisiert. [LinuxServer-Datenbankanforderungen](https://docs.linuxserver.io/images/docker-unifi-network-application/#setting-up-your-external-database)

## MongoDB und Zugangsdaten

Der Chart legt keine MongoDB und keinen MongoDB-Benutzer an. Für den Standardnamen `unifi` benötigt der Anwendungsbenutzer die folgenden Rollen:

| Datenbank | Rolle |
| --- | --- |
| `admin` | `clusterMonitor` |
| `unifi` | `dbOwner` |
| `unifi_stat` | `dbOwner` |
| `unifi_audit` | `dbOwner` |
| `unifi_restore` | `dbOwner` |

Das Beispiel [create-mongodb-user.js](../examples/create-mongodb-user.js) legt den Benutzer in `admin` an. Bei einem anderen Basisnamen werden alle vier Datenbanknamen entsprechend angepasst. `externalDatabase.authSource` muss die Datenbank nennen, in der der Benutzer tatsächlich existiert; mit dem Beispiel ist dies `admin`. Der Anwendungsbenutzer benötigt keine MongoDB-Root-Rolle.

Eine berechtigte Person verbindet sich mit `mongosh` zur Datenbank, authentifiziert sich interaktiv als Datenbankadministrator und lädt anschließend die Beispieldatei. Der eigene Administratorname, Host und die TLS-Einstellungen kommen aus dem Datenbankbetrieb. Das Anwendungskennwort wird über `passwordPrompt()` eingegeben; es wird weder in die Beispieldatei noch in einen Shellbefehl geschrieben. Das Skript erstellt ausschließlich einen neuen Benutzer. Bereits vorhandene Benutzer müssen bewusst verwaltet werden. Initialisierungsskripte des MongoDB-Containers werden bei einem vorhandenen Datenverzeichnis nicht erneut ausgeführt. [MongoDB `createUser`](https://www.mongodb.com/docs/manual/reference/method/db.createUser/)

Es gibt zwei Darstellungen derselben Zugangsdaten:

- **MongoDB-Benutzeranlage:** Originalbenutzername und Originalkennwort.
- **Secret für dieses Chart:** Beide Werte einzeln URI-percent-encoded, weil das Image sie in MongoDB-Verbindungs-URIs einsetzt. Das ist keine Verschlüsselung. Ein bereits kodierter Wert darf nicht nochmals kodiert werden.

Verwende [create-database-secret.py](../examples/create-database-secret.py), nachdem Namespace und Datenbankbenutzer angelegt sind:

```sh
python3 examples/create-database-secret.py \
  --context YOUR_CONTEXT --namespace unifi --name unifi-database
```

Das Hilfsprogramm fragt beide Originalwerte verdeckt ab, kodiert sie mit `urllib.parse.quote(..., safe="")` und übergibt ein Kubernetes Secret ausschließlich über die Standardeingabe von `kubectl`. Es schreibt keine Geheimnisdatei, gibt die Werte nicht aus und überschreibt kein bestehendes Secret. Ein Passwort wie `a/b@c` wird für das Secret zu `a%2Fb%40c`; dieses Beispiel ist kein verwendbares Kennwort. Die Keys heißen `username` und `password`. Bei Nutzung eines Secret-Managers sind dessen Ausgabe und die Chartwerte `usernameKey`/`passwordKey` aufeinander abzustimmen.

```yaml
externalDatabase:
  host: mongodb.database.svc.cluster.local
  port: 27017
  database: unifi
  authSource: admin
  existingSecret: unifi-database
  usernameKey: username
  passwordKey: password
  tls: false
```

`tls: true` aktiviert TLS in den erzeugten Verbindungen. Der Datenbankserver muss dafür korrekt konfiguriert und sein Zertifikat für die Anwendung vertrauenswürdig sein. Ein zusätzlicher privater CA-Import oder eine Client-Zertifikatverwaltung wird durch diese boolesche Option nicht eingerichtet. Ein öffentlich erreichbarer MongoDB-Port ist für den Controllerbetrieb nicht erforderlich.

## Persistenz und Prozessspeicher

Das Volume wird unter `/config` eingebunden. Die Anwendung verwendet dort unter anderem `data/system.properties`, ihren Keystore, Logs und lokale Backups. Die fachlichen Daten liegen zusätzlich in der externen MongoDB.

Der Chart arbeitet mit genau einer Anwendungsinstanz und `Recreate`. Parallel laufende Controller auf derselben Datenbank und demselben Konfigurationsvolume sind kein unterstütztes Skalierungsverfahren. Ein größeres `replicaCount` ist deshalb nicht vorgesehen.

Startup-, Readiness- und Liveness-Probe rufen `/status` über HTTPS auf Port 8443 auf und verlangen im JSON den Wert `meta.up: true`. UniFi kann bereits während des Starts HTTP 200 liefern, obwohl `meta.up` noch `false` ist. Die Prüfung berücksichtigt daher den tatsächlichen Anwendungsstatus. Das Standardzeitfenster für den Start beträgt ungefähr 15 Minuten und lässt sich über die Probeparameter anpassen.

Die Standardwerte sind 512 MiB Start-Heap, 1024 MiB maximaler Heap und 2 GiB Container-Speicherlimit. Das Limit umfasst auch Metaspace, Threads, native Bibliotheken und weitere Prozesse. Wenn der Heap erhöht wird, muss das Containerlimit mit ausreichend Abstand steigen. Die Werte werden über `java.initialHeapMiB` und `java.maxHeapMiB` eingestellt; verwende dafür keine zusätzlichen `MEM_STARTUP`-/`MEM_LIMIT`-Variablen, die mit den Chartwerten konkurrieren.

Der LinuxServer-Container startet seine Initialisierung als root, richtet Dateien und Besitzrechte ein und nutzt PUID/PGID für die Anwendung. Ein pauschales `runAsNonRoot` oder ein schreibgeschütztes Root-Dateisystem kann diesen Start verhindern. Bei Pod-Security-Vorgaben ist diese tatsächliche Imageanforderung zu berücksichtigen. Das Volume muss die erforderlichen Besitzrechte unterstützen; bei Root-Squash oder vorbesitzten Daten sind Rechte und Mountverhalten vorab zu prüfen.

Mit `persistence.retain: true` bleibt ein vom Chart angelegter PVC bei der Helm-Deinstallation erhalten. Er bleibt damit belegter Speicher und enthält weiterhin sensible Daten. Bei Wiederinstallation kann `persistence.existingClaim` auf den bewahrten Claim zeigen. Das Chart erzeugt in diesem Fall keinen neuen PVC und übernimmt nicht dessen externen Lebenszyklus. Manuelles Löschen des PVC oder des Namespace ist von einer Helm-Retention-Annotation nicht geschützt; auch die Reclaim Policy des Storage-Providers muss bekannt sein.

`persistence.enabled: false` zusammen mit `persistence.testOnlyEphemeral: true` ist für wegwerfbare Tests reserviert. Ohne persistentes `/config` geht bei einem Podwechsel unter anderem der lokale Konfigurationsstand verloren, selbst wenn MongoDB weiter existiert.

## Webzugriff mit TLS

Die Weboberfläche und API des Containers verwenden **HTTPS auf 8443**. Das Image erzeugt zunächst ein selbstsigniertes Zertifikat. **8080 ist der Inform-Port der Geräte und kein HTTP-Port für die Weboberfläche.** Ein Reverse Proxy muss deshalb für die Weboberfläche `https://...:8443` als Backend verwenden.

### Ingress im Cluster

[values-ingress.yaml](../examples/values-ingress.yaml) ist eine Ergänzung zur Datenbank- und Storagekonfiguration. Erstelle das Zertifikats-Secret separat, beispielsweise über den bestehenden Zertifikatsprozess oder mit `kubectl create secret tls`. Private Schlüssel werden nicht in Values-Dateien übernommen. Der Secretname steht unter `ingress.tlsSecretName`; Hostname und Zertifikat müssen zusammenpassen.

Bei ingress-nginx stellt `backend-protocol: HTTPS` den verschlüsselten Backendzugriff ein. Für das selbstsignierte Zertifikat ist `proxy-ssl-verify: "off"` angegeben. Dies verschlüsselt die Verbindung, prüft aber die Identität des Backends nicht. Wird ein vertrauenswürdiges Backendzertifikat eingerichtet, kann dessen Prüfung mit passenden CA- und Namenseinstellungen aktiviert werden. Ein anderer Ingress-Controller benötigt seine eigenen entsprechenden Einstellungen. Uploadgrößen und Zeitlimits des Beispiels sind für Backup-Uploads und langlebige Verbindungen anzupassen. [ingress-nginx-Annotationen](https://kubernetes.github.io/ingress-nginx/user-guide/nginx-configuration/annotations/)

Der Ingress leitet ausschließlich Webverkehr. TCP 8080, UDP 3478 und gegebenenfalls weitere Geräteports müssen über den Service und das Routing gesondert erreichbar bleiben.

### Bereits vorhandener externer NGINX

[nginx-unifi.conf](../examples/nginx-unifi.conf) wird innerhalb des `http`-Kontexts einer bestehenden NGINX-Konfiguration eingebunden. Vor Verwendung werden Domain, Zertifikatspfade und Backendadresse ersetzt. Das Beispiel übernimmt HTTPS-Terminierung am Proxy, HTTPS zum Backend sowie die für WebSockets erforderlichen Upgrade-Header. Vor dem Reload wird `nginx -t` ausgeführt. Der externe Proxy benötigt eine Route zur gewählten Serviceadresse; ein normaler ClusterIP ist außerhalb des Clusters nicht automatisch erreichbar. [NGINX-WebSocket-Dokumentation](https://nginx.org/en/docs/http/websocket.html)

Die dargestellte HTTP-Weiterleitung auf HTTPS gilt nur für Port 80 des Browserhosts. Inform-Anfragen auf 8080 dürfen nicht auf die Loginseite, einen OIDC-Proxy oder HTTPS 443 umgeleitet werden. Wenn externe NGINX- und Ingress-Terminierung kombiniert werden, müssen TLS, Forwarded-Header und Timeouts über beide Stationen geprüft werden.

## Geräteanbindung und Abnahme

Vergib einen stabilen Inform-Host, zum Beispiel einen intern auflösbaren Namen, der zur Serviceadresse führt. Die Geräte müssen diesen aus ihren jeweiligen VLANs erreichen. Pod-IP und nur intern auflösbare Kubernetes-Namen eignen sich dafür normalerweise nicht.

TCP 8080 wird auf beiden Seiten unverändert bereitgestellt. Ein automatisch zugewiesener NodePort ist keine vollständige Inform-Konfiguration: Die Anwendung kann sonst eine andere Adresse oder einen anderen Port an Geräte zurückmelden. Verwende vorzugsweise einen erreichbaren LoadBalancer auf dem Standardport oder eine ausdrücklich konfigurierte Weiterleitung. Falls abweichende Ports erforderlich sind, müssen die Anwendungseinstellungen und Geräteadresse dazu passen.

Prüfe nach Bereitstellung aus den echten Gerätenetzen:

1. DNS-Auflösung und Routing zur vorgesehenen Adresse.
2. TCP 8080 und UDP 3478 einschließlich Rückweg und Firewallregeln.
3. Erreichbarkeit der Weboberfläche über den gewählten TLS-Weg.
4. Das tatsächlich in UniFi eingestellte Inform-Ziel und den Online-Status der Geräte.
5. Optionale Gastportal-, Syslog- und Speedtest-Funktionen, sofern genutzt.

UDP 10001 unterstützt Discovery. Ein Service transportiert jedoch keine lokalen Broadcasts aus beliebigen VLANs in das Podnetz. Bereits bekannte Geräte können per Inform/DNS auf den Controller zeigen; für neue Geräte ist gegebenenfalls Layer-3-Adoption erforderlich. [Ubiquiti Layer-3-Adoption](https://help.ui.com/hc/en-us/articles/204909754-Remote-Adoption-Layer-3)

## Backup und Wiederherstellung

Ein vollständiger Wiederherstellungsplan umfasst drei Arten von Sicherung:

| Sicherung | Zweck |
| --- | --- |
| Network-Backup `.unf` | Portabler Konfigurations- und Migrationsweg über die Anwendung; benötigten Historienumfang beim Export wählen |
| Externe MongoDB | Alle Anwendungsdatenbanken und der passende Benutzer-/Rollenstand im Datenbank-Backupverfahren |
| `/config` | Persistierte Verbindungseinstellungen, Keystore, zusätzliche Dateikonfiguration und lokale Backups |

Speichere auch Chartversion, Image-Digest, verwendete Values ohne Geheimnisse und die zugehörige Datenbankversion. Datenbank- und Config-Sicherung müssen einen zueinander passenden Zeitpunkt abbilden. Ein praktikables Wartungsverfahren stoppt die Anwendung, erstellt ein konsistentes Datenbank-Backup und sichert `/config`, bevor die Anwendung wieder startet. Bei produktiver MongoDB sind dazu deren etabliertes Backupverfahren und gegebenenfalls Replikations-/Snapshotregeln zu verwenden. Das Chart automatisiert diese Abläufe nicht.

Backups werden außerhalb des Anwendungsvolumes gespeichert, geschützt und regelmäßig in einer isolierten Umgebung wiederhergestellt. Eine PVC-Snapshotdatei allein ersetzt weder die externe MongoDB-Sicherung noch einen Restore-Test. Die Dateien enthalten sensible Konfigurationen und teilweise Zugangsdaten; sie gehören nicht ins Repository oder in öffentliche CI-Artefakte.

## Rotation von Datenbankzugangsdaten

Das gepinnte LinuxServer-Startskript erzeugt `/config/data/system.properties` nur, wenn diese Datei noch nicht existiert. Die Verbindungswerte bleiben anschließend dort erhalten. Eine Secretänderung, ein Helm-Upgrade oder ein Podneustart allein aktualisiert bestehende URIs nicht. In der Datei enthalten `db.mongo.uri` und `statdb.mongo.uri` die Verbindungsdaten. [Startskript des Images](https://github.com/linuxserver/docker-unifi-network-application/blob/10.6.101-ls145/root/etc/s6-overlay/s6-rc.d/init-unifi-network-application-config/run)

Für eine Rotation mit Rückkehrmöglichkeit:

1. Ein passendes Datenbank-/Config-Backup erstellen und die aktuelle Secretreferenz festhalten.
2. Einen zweiten Anwendungsbenutzer mit den gleichen benötigten Rollen und einem neuen Kennwort in MongoDB anlegen. Die alten Zugangsdaten zunächst gültig lassen.
3. Ein neues Secret, etwa `unifi-database-v2`, mit den URI-kodierten neuen Zugangsdaten erzeugen. Keine Kennwörter über `--set` oder Values-Dateien übergeben.
4. Die Anwendung im Wartungsfenster stoppen und ihren vollständigen Podabschluss abwarten. Eine automatische GitOps-Reconciliation muss diesen Wartungszustand respektieren.
5. Das `/config`-Volume ausschließlich für die Wartung einbinden. Mit einem geschützten Bearbeitungsweg beide MongoDB-URIs auf den neuen Benutzer und das neue Kennwort ändern. Andere Einstellungen, Dateirechte und Keystore erhalten. Weder den Dateiinhalt noch die vollständigen URIs in Logs oder Tickets ausgeben. Die Datei nicht einfach löschen, da sie weitere Anwendungseinstellungen enthalten kann.
6. Die Helm-/GitOps-Konfiguration auf das neue `existingSecret` und gegebenenfalls geänderte Host-/TLS-Werte aktualisieren, den Wartungsmount entfernen und die eine Anwendungsinstanz starten.
7. Datenbankanmeldung, UI, Geräteverbindungen und Neustart-Persistenz prüfen. Erst danach den alten MongoDB-Benutzer und das nicht mehr benötigte Secret entfernen.

Bei Problemen wird die neue Anwendung gestoppt, der gesicherte Configstand und die alte Secretreferenz wiederhergestellt und mit dem weiterhin vorhandenen alten Benutzer gestartet. Eine Datenbankmigration durch einen gleichzeitigen Versionswechsel macht diesen Ablauf komplexer; rotiere Zugangsdaten daher möglichst getrennt von Versionsupdates.

## Migration eines bestehenden Controllers

Der bekannte Quellstand ist **UniFi Network 9.4.19**. Der Hosttyp, die verwendeten Zusatzfunktionen und die konkrete Backupkompatibilität müssen vor dem Umzug vollständig geprüft werden. Falls der bestehende Host ein Cloud Gateway mit eingebautem Network-Controller ist, lässt sich dessen Gatewayverwaltung nicht einfach an einen externen Controller übertragen. Protect und andere UniFi-Anwendungen sind ebenfalls kein Bestandteil dieses Charts. [Ubiquiti Self-Hosting](https://help.ui.com/hc/en-us/articles/34210126298775-Self-Hosting-UniFi)

Die folgende Reihenfolge ist eine Betriebsplanung; sie wurde nicht am Quellcontroller durchgeführt:

1. Geräte, Sites, SSIDs, VLANs, Portprofile, Gateway-/DHCP-Funktionen, Geräte-SSH-Zugang, Inform-Ziel, lokale Administratoren und benötigte Zusatzfunktionen erfassen.
2. Ein frisches **Network-Backup `.unf`** herunterladen, bei Bedarf einschließlich verfügbarer Historie. Ein UniFi-OS-Systembackup ist ein anderer Sicherungsumfang. Eine bloße manuelle Abschrift der Einstellungen ersetzt das Network-Backup nicht.
3. Die Wiederherstellung mit der konkret geplanten Zielversion in einer isolierten Testinstallation prüfen. Keine Live-Geräte umstellen und keine zweite aktive Instanz auf die alte Datenbank zeigen lassen. Quell- und Zielversion müssen für diesen Restorepfad geeignet sein; ein Major-Versionssprung wird nicht allein durch erfolgreiches Helm-Rendering bestätigt.
4. Für das eigentliche Ziel frisches `/config` und eine passend vorbereitete Datenbank verwenden. Eine alte Container-Datenstruktur mit eingebauter MongoDB wird nicht direkt als `/config` übernommen.
5. Im Wartungsfenster ein finales Backup erstellen, wiederherstellen und Konfiguration, Administratorzugang sowie benötigten Historienumfang vergleichen.
6. Inform-Host, DNS oder die bisherige Erreichbarkeit kontrolliert auf das Ziel umstellen. Der alte Controller darf nach der Übergabe nicht parallel Änderungen an denselben Geräten auslösen. Alten Datenstand für die Rückkehr aufbewahren.
7. Alle Geräte müssen im Ziel online erscheinen. Danach WLAN, VLAN-Zuordnung, Gateway/DHCP, Portprofile, gegebenenfalls Gastportal und Controllerneustart praktisch testen. Geräte nicht vorschnell zurücksetzen oder aus dem Altbestand löschen.
8. Erst nach erfolgreicher Abnahme den Altcontroller endgültig stilllegen und die neue Backupüberwachung übernehmen.

Ein Network-Backup überträgt Anwendungseinstellungen und Gerätekonfigurationen. Der zusätzliche Inform-/DNS-Wechsel sorgt dafür, dass Geräte ihren neuen Controller erreichen. In Problemfällen helfen die vorher gesicherten Geräte-SSH-Zugangsdaten. Ein Werksreset ist kein regulärer erster Migrationsschritt. [Ubiquiti Backup- und Migrationsverfahren](https://help.ui.com/hc/en-us/articles/360008976393-Backups-and-Migration-in-UniFi)

Ein Rückkehrplan stoppt zunächst die Zielinstanz und stellt den unveränderten Altcontroller samt passender Inform-/DNS-Zuordnung wieder bereit. **`helm rollback` setzt ausschließlich Kubernetes-/Helm-Konfiguration zurück und macht keine MongoDB-Schemaänderung rückgängig.** Nach einem Versionsupdate sind bei Bedarf Datenbank und `/config` aus dem zusammengehörigen Backup auf einer kompatiblen Anwendungsversion wiederherzustellen.

## Updates und Fehleranalyse

Updates werden als neues festes Anwendungsimage mit passendem Digest und Chartversion geplant. Wird `image.digest` gesetzt, bestimmt der Digest die tatsächlich gestarteten Bytes; eine Änderung nur des Tags reicht dann nicht. MongoDB wird getrennt aktualisiert. Vor beiden Änderungen werden Backups und Releasehinweise geprüft. Für längere Wiederherstellungen kann `probes.startup.failureThreshold` angepasst werden; eine wiederholt fehlschlagende Datenbankanmeldung wird durch ein längeres Zeitfenster jedoch nicht gelöst.

| Beobachtung | Zuerst prüfen |
| --- | --- |
| Pod startet nicht oder ist `Pending` | PVC-Bindung, StorageClass, Node-Ressourcen, Pod-Security-Regeln |
| Initialisierung wartet auf MongoDB | DNS, Host, Port und Routing aus dem Podnetz |
| Authentifizierungsfehler | Originalkennwort bei Benutzeranlage, einmalige URI-Kodierung im Secret, `authSource`, Rollen sowie bestehende `system.properties` |
| UI über Ingress liefert 502 | HTTPS-Backend 8443, selbstsigniertes Zertifikat, Serviceendpoints und Readiness |
| UI erreichbar, Geräte offline | Inform-Host, TCP 8080, UDP 3478, Geräte-VLAN-Routing, Firewalls und alter Controller |
| `OOMKilled` | Containerlimit, Heap und zusätzlicher Prozessspeicher |
| Neue Secrets wirken nicht | Persistierte URIs und kontrollierten Rotationsablauf prüfen |

Die separat aufrufbare Clusterintegration ist in [scripts/integration/suseai-smoke.py](../scripts/integration/suseai-smoke.py) implementiert. Sie erstellt einen neuen Testnamespace mit eigener MongoDB und testet das gepackte Chart. Vor der Persistenzprüfung schließt sie den lokalen Ersteinrichtungsassistenten des Wegwerf-Testcontrollers mit zufällig erzeugten Zugangsdaten ab. Dabei werden weder ein Cloudkonto angebunden noch ein WLAN erstellt oder Geräte adoptiert. Dieser Schritt stellt einen eingerichteten Controller her: Eine unvollständige Einrichtung bleibt im Factory-Default-Zustand und kann ihre Site beim Neustart erneut anlegen. Vor Ausführung werden Zielkontext, Node und StorageClass kontrolliert. Die Prüfung nutzt weder ein Produktionsbackup noch die bestehenden Geräte. Eine positive Prüfung einer frischen Installation ist deshalb keine Freigabe des noch ausstehenden Controllerumzugs.

Der lokale Ablauf lautet `make bootstrap`, `make validate`, `make package` und anschließend ein bewusst konfiguriertes `make smoke`. Das Releasepaket liegt unter `build/packages/unifi-network-application-1.0.0.tgz`. Die Veröffentlichung verwendet dieses bereits geprüfte Archiv zusammen mit `summary.json` und `validation.json`; Source-Commit, Tag und Prüfberichte müssen übereinstimmen. Das Archiv wird bei der Veröffentlichung nicht neu gebaut. Nach Veröffentlichung stehen die Nachweise beim [GitHub Release 1.0.0](https://github.com/CodeOpsMS/unifi-network-application-helm-chart/releases/tag/1.0.0) zum Download bereit. Die genaue Vorgehensweise steht in [CONTRIBUTING.md](../CONTRIBUTING.md#release-procedure).
