# ABL Wallbox für Home Assistant

Lokale, kostenlose HACS-Integration für ABL eMH1 mit EVCC2/3-Controller. Steuert die Wallbox direkt über USB/RS485 oder eine transparente Netzwerkbrücke. Kein MQTT, keine Cloud und keine evcc-Lizenz erforderlich.

## Funktionen

| Bereich | Funktionen |
| --- | --- |
| Laden | Ladefreigabe, Ladestromvorgabe in ganzen Ampere, Pause über 0 A |
| Installation | Feste Stromgrenze getrennt vom Nennstrom; niemals automatisch beim Einrichten verändert |
| Controller | Betriebsart Standalone / RFID / SBC, Busadresse 1–16, Fallback und Ladefreigabe nach Neustart |
| Schieflast | Aus / 20 A; bei passender Firmware zusätzlich 16 A |
| Status | Betriebszustand, Fahrzeugverbindung, effektive Stromfreigabe, PWM und Freigabeeingänge |
| Messwerte | Phasenströme, sofern vom Controller verfügbar; geschätzte Leistung aus diesen Strömen und 230 V |
| Fehler | Aktive Störungen und die 17 im Controller gespeicherten Fehlerzähler; ausdrücklich bestätigtes Löschen der Zähler |
| Diagnose | Bus-Suche, Controller-Neustart, Diagnose-Download und in HA beobachteter Fehlerverlauf |

Die Einstellungen des Charge-Controller-Bereichs der ABL Configuration Software sind als HA-Entitäten verfügbar. Firmware, Hardware und Nennstrom werden ausgelesen und sind keine veränderbaren Einstellungen. RFID-Modus erfordert die dazugehörige Hardware; RFID-Kartenverwaltung und separate Energiezähler gehören zu anderen Geräten und werden durch diese Controller-Integration nicht nachgebildet. Firmware-Updates werden nicht angeboten.

## Verbindung

- **TCP:** transparente Übertragung von ABL Modbus **ASCII**. Host und TCP-Port der Brücke angeben. Die physische RS485-Seite der Brücke auf **38400 Baud, 8 Datenbits, gerade Parität, 1 Stoppbit (8E1)** stellen.
- **USB/seriell:** stabiles Gerät wie `/dev/serial/by-id/...` verwenden. RFC2217 wird über einen entsprechenden `rfc2217://...`-Gerätepfad unterstützt.
- **Shelly Pro RS485 Add-on:** eigener Zugangsweg über das mitgelieferte [UART-Brückenskript](shelly/README.md), inklusive Unterstützung der vorhandenen Shelly-Digest-Authentifizierung. Der native Modbus-RTU-Modus wird dafür nicht verwendet.
- Die Integration ist unabhängig vom Einplatinencomputer. Beispielsweise kann ein Tinker Board mit `ser2net` als transparente Brücke dienen. Auch andere serielle Netzwerkserver benötigen einen transparenten Modus; eine reine Modbus-TCP-zu-Modbus-RTU-Umsetzung funktioniert mit dem ABL-ASCII-Protokoll nicht.
- Ein Coolwell-RS485/Ethernet-Konverter ist über den TCP-Zugangsweg nutzbar, sofern sein transparenter TCP-Server-Modus das ASCII-Protokoll unverändert weiterleitet. Die Modellbezeichnung allein ersetzt diese notwendige Betriebsart nicht; dieser Adapter wurde nicht physisch getestet.
- Nur eine Anwendung darf die Brücke belegen. Vor der Einrichtung die ABL-PC-Software, virtuelle COM-Umleitungen und andere ABL-Integrationen vom selben Anschluss trennen. Mehrere Controller an einem Bus teilen sich innerhalb dieser Integration eine Verbindung.

## Installation über HACS

1. In HACS über das Menü **Benutzerdefinierte Repositories** die Adresse `https://github.com/Christians8710/abl-wallbox-ha` mit Typ **Integration** hinzufügen.
2. **ABL Wallbox** herunterladen und Home Assistant neu starten.
3. Unter **Einstellungen → Geräte & Dienste → Integration hinzufügen → ABL Wallbox** Host und Port der Brücke oder das serielle Gerät sowie die Controller-Busadresse angeben.
4. Beim Einrichten werden ausschließlich Werte gelesen. Vorhandene Stromgrenzen, Betriebsart, Schieflastschutz und Ladefreigabe bleiben bestehen.

Die bisherige Integration `ev_charger_modbus` muss für denselben Anschluss deaktiviert sein. Bereits vorhandene Automationen verwenden deren alte Entitäten weiter; sie müssen bei Bedarf auf die neuen ABL-Wallbox-Entitäten umgestellt werden.

## Die unterschiedlichen Stromwerte

- **Nennstrom:** bauliche Obergrenze des Controllers, beispielsweise 32 A.
- **Feste Stromgrenze:** im Controller gespeicherte Begrenzung für die Installation, beispielsweise 16 A. Nur auf einen für Anschluss und Installation freigegebenen Wert ändern.
- **Ladestromvorgabe:** dynamische Vorgabe von HA, beispielsweise 6 A. Einstellbar sind 0 oder 6 bis zur festen Stromgrenze. 0 A bedeutet Ladepause.
- **Effektive Stromfreigabe:** tatsächlich vom Controller angebotene Stromstärke nach dessen weiteren Begrenzungen.

Ein 32-A-Controller mit fester 16-A-Grenze erlaubt daher eine Ladestromvorgabe bis 16 A. Die Integration liest diese Werte getrennt aus; Registeradresse und Statusbits werden nicht als Stromstärke interpretiert.

Wird die feste Stromgrenze gesetzt, senkt die Integration eine darüberliegende gespeicherte Ladestromvorgabe auf diese Grenze und prüft den zurückgelesenen Wert. Eine kleinere Vorgabe oder Ladepause bleibt erhalten. Das Erhöhen der festen Grenze erhöht die Ladestromvorgabe nicht automatisch. Einrichtung und reine Abfragen verändern weiterhin keine Einstellungen.

Fehlende Strommesswerte werden als unbekannt dargestellt. Der vom Controller gelieferte Sonderwert für fehlende Messung wird niemals als 100 A ausgegeben. Die geschätzte Leistung ersetzt keinen Energiezähler.

## Einstellungen und Aktionen

Feste Einstellungen lassen sich nur ohne angeschlossenes Fahrzeug ändern. Die Integration wechselt dafür kontrolliert in den Konfigurationsmodus, prüft die zurückgelesenen Werte und stellt die vorherige Ladefreigabe wieder her. Eine zuvor gesperrte Wallbox wird dadurch nicht freigegeben. Diese Bedingungen gelten auch für eine Änderung der Busadresse.

Die Aktionen `abl_wallbox.configure_controller`, `abl_wallbox.scan_bus` und `abl_wallbox.clear_fault_counters` stehen in **Entwicklerwerkzeuge → Aktionen** bereit. Bei der Konfiguration nur die tatsächlich gewünschten Felder angeben. Unveränderte Felder werden nicht überschrieben.

`clear_fault_counters` erfordert `confirm: true` und löscht die Fehlerzähler dauerhaft im Controller. Der HA-Verlauf bleibt erhalten. Der Controller liefert Zählerstände ohne Zeitstempel; Zeitstempel im HA-Fehlerverlauf geben ausschließlich an, wann HA eine Änderung beobachtet hat. Zähler können Neustarts überstehen und beziehen sich nicht nur auf den aktuellen Ladevorgang.

## Unterstützte und geprüfte Hardware

Das Protokoll richtet sich an ABL EVCC2/3. Live geprüft sind TCP-Kommunikation, Status- und Fehlerzählerabfragen sowie das Schreiben der vorhandenen Stromgrenze, Betriebsart und Schieflastgrenze an einer eMH1 1W2208, PCBA 160307, Firmware 4.1. Der Schreibtest stellt die ursprüngliche Ladefreigabe und Ladestromvorgabe wieder her und prüft, dass alle Einstellungen und Fehlerzähler erhalten geblieben sind. Weitere Controller werden anhand ihrer Firmwaredaten erkannt; nicht unterstützte Schreibbefehle führen zu einer Fehlermeldung statt einer Erfolgsmeldung. Geräte wie eM4 verwenden andere Schnittstellen.

## Entwicklung

Die Protokollbibliothek benötigt Home Assistant nicht. Ihre Tests prüfen echte Frame-Formate, getrennte Stromgrenzen, ungültige Antworten, verzögerte Antworten, Adresskonflikte, verlorene Bestätigungen und die Wiederherstellung des Freigabezustands. Separate Tests prüfen die Einrichtung und Entitäten mit Home Assistant.

```sh
python -m pip install -r requirements-dev.txt
python -m pytest tests/test_protocol.py
python -m ruff check custom_components tests
```

Die HA-Tests benötigen Linux und die in der CI festgelegte stabile Home-Assistant-Version. Die Integration wird nicht zum Laden auf 32 A vorausgesetzt oder automatisch auf 32 A eingestellt.

## Protokollreferenzen

- [ABL: Schnittstellenbeschreibung Modbus ASCII](https://www.ablmobility.de/global/downloads/anleitungen/emh1/Schnittstellenbeschreibung_Modbus-ASCII.pdf)
- [ABL Configuration Software – Anleitung](https://www.ablmobility.de/global/downloads/anleitungen/zubehoer/Bedienungsanleitung_ABL-ConfigurationSoftware_DE.pdf)
- [micerny/abl-charger: unabhängiges Protokollbeispiel](https://github.com/micerny/abl-charger)

Eigenständige Implementierung. Keine Herstellerprogrammdateien werden mitgeliefert. Nicht mit ABL verbunden. MIT-Lizenz.
