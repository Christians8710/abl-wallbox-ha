# WallboxPilot · Board-Update 1.2.1

Version 1.2.1 verbessert beide Oberflächen mit hellem und dunklem Design, deutlicheren Feldumrandungen und konkreten Aktionsrückmeldungen. Das Board folgt der Systemeinstellung; die Dashboard-Karte folgt dem HA-Design. Petrol und das originale Logo bleiben erhalten.

Bei bereits aktivem eigenständigem Controller genügt das normale Update ohne `--standalone`. Der bestehende HA-Eintrag bleibt bestehen; die nachfolgenden Umstellungsschritte sind nur beim ersten Wechsel von der seriellen Brücke erforderlich.

Dieses Paket aktualisiert eine bereits eingerichtete ABL-CONFCAB-Brücke auf einem Debian-/Armbian-Board. Es enthält **kein SD-Karten-Abbild** und installiert weder ein Betriebssystem noch fehlende Abhängigkeiten.

## Änderungen

Optionaler eigenständiger Controller mit eigener Wallbox-Bedienseite und lokaler API. Der Board-Dienst besitzt allein die serielle Verbindung; HA ist ein API-Client. Ohne ausdrückliche Aktivierung bleibt der bisherige transparente Brückenbetrieb erhalten. Alle Ansichten verwenden das vorhandene Petrol-Design und Projektlogo.

## Zum eigenständigen Controller wechseln

1. HA auf Integration **1.2.0** aktualisieren. Den bestehenden Wallbox-Eintrag sichern und vorübergehend deaktivieren. ABL-PC-Software und COM-Umleitungen schließen. Den Eintrag nicht löschen.
2. Das Board-ZIP entpacken. Mit `sudo /usr/bin/python3 install-update.py --check --standalone --address 1` vorprüfen. `--address` ist die vorhandene Busadresse und programmiert keine neue Adresse. Bei einem anderen USB-Adapterport zusätzlich `--controller-port PORT` angeben.
3. Mit `sudo /usr/bin/python3 install-update.py --standalone --address 1` aktivieren. Der Installer sichert Programme und Brückenkonfiguration, startet den Dienst neu und prüft eine echte Antwort der Wallbox. Bei einem Fehler stellt er den bisherigen Betrieb wieder her.
4. Den lokalen Zugriffsschlüssel am Board mit `sudo cat /var/lib/abl-bridge/controller/api-key` auslesen. Er wird nicht in URLs, Logmeldungen oder Diagnose-Downloads ausgegeben. `http://abl-bridge.local:8080/` öffnen und damit anmelden; alternativ die aktuelle IP des Boards verwenden.
5. In HA beim **bestehenden** Wallbox-Eintrag **Neu konfigurieren** wählen: **WallboxPilot / lokale API**, Host des Boards, Port **8080**, vorhandene Busadresse und Zugriffsschlüssel. Den Eintrag wieder aktivieren. Geräte- und Entitätskennungen bleiben erhalten.

Danach liegt die Wallbox-Steuerung unter `/` und der Boardstatus unter `/bridge/`. Die WLAN-Einrichtung bleibt bei Comitup. Der rohe serielle TCP-Port ist nur unter `127.0.0.1` auf dem Board erreichbar; die Windows-COM-Umleitung ist dann kein paralleler Zugangsweg. Die API ist für das lokale Netz vorgesehen.

Der Dienst liest beim Start und nach einem Ausfall die vorhandenen Werte aus. Er erteilt dabei keine neue Ladefreigabe und setzt keine Stromgrenzen. Einstellungen werden weiterhin im Controller geprüft und zurückgelesen. PV- und Zeitsteuerung kommen später.

## Lokale API

API-Abfragen benötigen `Authorization: Bearer <Zugriffsschlüssel>`. Browser verwenden eine zeitlich begrenzte HttpOnly-Sitzung und zusätzlich einen CSRF-Schlüssel für Änderungen. Zugriffsschlüssel gehören nicht in URLs.

| Anfrage | Zweck |
| --- | --- |
| `GET /api/v1/state` | Cache mit `available`, `observed_at`, Controllerdaten und Fehlerverlauf |
| `GET /api/v1/info` | API-Version und stabile Board-Kennung |
| `POST /api/v1/command` | Ausdrücklicher Steuer- oder Diagnosebefehl |

Ein Befehl enthält `request_id` (16–80 Zeichen aus Buchstaben, Ziffern, `_` und `-`), `address` und `operation`. Aktionen: `set_current` (ganzzahliges `value`: 0 oder 6 bis zur tatsächlichen Grenze), `set_enabled` (boolesches `value`), `configure` (`settings` mit den im Projekt dokumentierten Einstellfeldern), `refresh`, `scan`, `restart` und `clear_fault_counters`. Neustart und Zählerlöschung erfordern zusätzlich `confirm: true`.

Die letzten **128 Anfragekennungen** bleiben auf dem Board gespeichert. Eine identische Wiederholung liefert das vorhandene Ergebnis und schreibt nicht erneut. Dieselbe Kennung mit anderen Daten wird abgewiesen. Eine durch einen Dienstabbruch unterbrochene Anfrage wird als unklar markiert und nicht automatisch wiederholt. Nach verlorener Antwort dieselbe Kennung verwenden und den tatsächlichen Zustand prüfen. Die Browserseite bietet **Ergebnis der Anfrage prüfen**. Alte Daten sind mit `available: false` gekennzeichnet und sperren die Bedienung.

Board-Kennung, Busadresse, die letzten 100 Fehlerbeobachtungen und Anfragekennungen liegen unter `/var/lib/abl-bridge/controller/`. Zeitpunkte sind Beobachtungszeitpunkte; der Controller liefert keine Fehlerzeitstempel. Das Löschen der Controllerzähler entfernt den aufgezeichneten Verlauf nicht.

## Netzwerkdiagnose

Die rein lesende Diagnose kann mit `sudo systemctl enable --now abl-network-diagnostics.service` aktiviert werden. Sie protokolliert Netzwerk-/Kernelmeldungen und einmal pro Minute Laufzeit, WLAN-Verbindung, Gateway-Prüfung, Temperatur, Speicher und lokalen Brückenstatus. `/var/lib/abl-bridge/network-diagnostics.jsonl*` liegt außerhalb des Armbian-RAM-Logs, ist nur für root lesbar und auf drei Dateien zu je ungefähr 2 MB begrenzt. Die Dateien bleiben lokal und können Netzwerknamen und Adressen enthalten. Der Dienst verändert weder WLAN noch Wallbox und startet nichts automatisch neu.

## Voraussetzungen

Die vorhandenen Dienste `abl-bridge` und `comitup-web`, die Programme unter `/usr/local/sbin/`, die Konfiguration `/etc/abl-bridge.json` und die bereits installierten Debian-Pakete für Comitup, D-Bus und Flask müssen vorhanden sein. Für das Update das System-Python `/usr/bin/python3` verwenden, damit diese Pakete verfügbar sind.

## Aktualisieren

1. Das Board-ZIP aus dem passenden GitHub-Release auf das Board übertragen und in einen eigenen Ordner entpacken.
2. In den entpackten Ordner wechseln und ohne Änderungen vorprüfen:

   ```sh
   sudo /usr/bin/python3 install-update.py --check
   ```

3. Das eigentliche Update starten:

   ```sh
   sudo /usr/bin/python3 install-update.py
   ```

Das Programm sichert die betroffenen vorhandenen Dateien mit ihren Zugriffsrechten unter `/var/backups/abl-bridge/`. Es installiert die lokalen UI-Dateien nach `/usr/local/share/abl-bridge/ui/` und ersetzt die beiden Programme unter `/usr/local/sbin/`. Nur zuvor aktive Brücken-/Portaldienste werden neu gestartet. Eine abgeschaltete WLAN-Einrichtungsseite wird nicht gestartet. Die Verbindung zu Home Assistant wird kurz unterbrochen.

Beim reinen Update werden Statusseite, Logo, Stylesheet, USB-/TCP-Portzuordnungen sowie unveränderte WLAN- und Brückenkonfiguration geprüft. Mit `--standalone` werden zusätzlich die ausdrücklich beschriebenen Controller-Einstellungen der Brücke aktiviert und eine echte Wallbox-Antwort geprüft. Bei Fehlern werden Programme, UI-Dateien und Brückenkonfiguration wiederhergestellt. Es gibt keinen Board-Neustart und keine schreibenden Wallbox-Befehle. Netzwerkprofile und Passwörter werden weder ersetzt noch veröffentlicht.

Nach Erfolg die bisherige Statusadresse, beispielsweise `http://abl-bridge.local:8080/`, neu laden. Die installierte Paketversion steht in `/usr/local/share/abl-bridge/VERSION`.

## Schutzmechanismen und Lizenzen

Der manuelle Board-Neustart erfordert weiterhin Bestätigung, einen passenden Host und Ursprung sowie ein CSRF-Token. Doppelte Anfragen führen nicht zu mehrfach geplanten Neustarts. Die WLAN-Formulare verwenden die vorhandenen Comitup-Routen und Feldnamen. Verbindungsansichten zeigen kein Passwort an.

WallboxPilot-Oberflächen und Installer: © 2026 Christians8710, [MIT-Lizenz](LICENSE). Der installierte WLAN-Dienst Comitup bleibt unter GPL-2.0 oder neuer. Dessen Pakete und vorhandene Lizenzhinweise werden nicht ersetzt. Einzelheiten zur Gestaltung stehen in [ui/README.md](ui/README.md).

## Prüfungen

```sh
python3 bridge/test_update.py
python3 bridge/test_reboot_button.py
```

Diese Tests laufen aus dem Repository-Hauptordner. Installer-Tests schreiben ausschließlich in einen temporären Ordner; Neustarttests ersetzen alle echten Neustartbefehle.
