# ABL-Netzwerkbrücke · Update 1.1.0

Dieses Paket aktualisiert eine bereits eingerichtete ABL-CONFCAB-Brücke auf einem Debian-/Armbian-Board. Es enthält **kein SD-Karten-Abbild** und installiert weder ein Betriebssystem noch fehlende Abhängigkeiten.

## Änderungen

Petrol-Oberfläche mit dem unveränderten Projektlogo für Statusseite und WLAN-Einrichtung. Der Status aktualisiert sich ohne vollständiges Neuladen. Geöffnete Bereiche und Tastaturfokus bleiben erhalten. USB-Erkennung und eine tatsächliche Antwort der Wallbox werden weiterhin unterschieden. Die Brücke enthält keine Ladesteuerung; diese bleibt in Home Assistant.

## Voraussetzungen

Die vorhandenen Dienste `abl-bridge` und `comitup-web`, die Programme unter `/usr/local/sbin/`, die Konfiguration `/etc/abl-bridge.json` und die bereits installierten Debian-Pakete für Comitup, D-Bus und Flask müssen vorhanden sein. Für das Update das System-Python `/usr/bin/python3` verwenden, damit diese Pakete verfügbar sind.

## Aktualisieren

1. Das Brücken-ZIP aus dem passenden GitHub-Release auf das Board übertragen und in einen eigenen Ordner entpacken.
2. In den entpackten Ordner wechseln und ohne Änderungen vorprüfen:

   ```sh
   sudo /usr/bin/python3 install-update.py --check
   ```

3. Das eigentliche Update starten:

   ```sh
   sudo /usr/bin/python3 install-update.py
   ```

Das Programm sichert die betroffenen vorhandenen Dateien mit ihren Zugriffsrechten unter `/var/backups/abl-bridge/`. Es installiert die lokalen UI-Dateien nach `/usr/local/share/abl-bridge/ui/` und ersetzt die beiden Programme unter `/usr/local/sbin/`. Nur zuvor aktive Brücken-/Portaldienste werden neu gestartet. Eine abgeschaltete WLAN-Einrichtungsseite wird nicht gestartet. Die Verbindung zu Home Assistant wird kurz unterbrochen.

Danach werden die Statusseite, das Logo, das Stylesheet, unveränderte USB-/TCP-Portzuordnungen sowie unveränderte WLAN- und Brückenkonfiguration geprüft. Schlägt eine Prüfung fehl, stellt das Update die betroffenen Programm-/UI-Dateien automatisch wieder her. Es führt keinen Board-Neustart und keine Wallbox-Befehle aus. Konfigurationsdateien, Netzwerkprofile und Passwörter werden weder ersetzt noch veröffentlicht.

Nach Erfolg die bisherige Statusadresse, beispielsweise `http://abl-bridge.local:8080/`, neu laden. Die installierte Paketversion steht in `/usr/local/share/abl-bridge/VERSION`.

## Schutzmechanismen und Lizenzen

Der manuelle Board-Neustart erfordert weiterhin Bestätigung, einen passenden Host und Ursprung sowie ein CSRF-Token. Doppelte Anfragen führen nicht zu mehrfach geplanten Neustarts. Die WLAN-Formulare verwenden die vorhandenen Comitup-Routen und Feldnamen. Verbindungsansichten zeigen kein Passwort an.

Neue ABL-Oberflächen und Installer: © 2026 Christians8710, [MIT-Lizenz](LICENSE). Der installierte WLAN-Dienst Comitup bleibt unter GPL-2.0 oder neuer. Dessen Pakete und vorhandene Lizenzhinweise werden nicht ersetzt. Einzelheiten zur Gestaltung stehen in [ui/README.md](ui/README.md).

## Prüfungen

```sh
python3 bridge/test_update.py
python3 bridge/test_reboot_button.py
```

Diese Tests laufen aus dem Repository-Hauptordner. Installer-Tests schreiben ausschließlich in einen temporären Ordner; Neustarttests ersetzen alle echten Neustartbefehle.
