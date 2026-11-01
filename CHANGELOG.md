# Änderungen

## 1.2.0

- Eigenständiger Board-Dienst mit lokaler Wallbox-Bedienseite, geprüften Befehlen, laufender Statusabfrage und dauerhaftem Fehlerverlauf ohne HA.
- Authentifizierte lokale API mit Browser-Anmeldung, CSRF-Schutz und dauerhaftem Wiederholschutz für die letzten 128 Anfragekennungen.
- Neue API-Verbindungsart in HA. Umkonfiguration des bestehenden Eintrags erhält alle 50 Entitäten und die Gerätezuordnung; bestehender HA-Verlauf bleibt erhalten. Adressänderungen desselben gebundenen Boards werden übernommen.
- Bisherige direkte TCP-, USB- und Shelly-Verbindungen bleiben verfügbar. Der ausdrückliche Wechsel zum Board-Modus begrenzt den rohen TCP-Port auf das Board selbst.
- Board-Installer sichert auch die Brückenkonfiguration und stellt sie bei fehlgeschlagener Aktivierung wieder her. Der Start des Controllers ändert keine Wallbox-Einstellungen.
- Begrenzte lokale Netzwerkdiagnose außerhalb des Armbian-RAM-Logs. Keine automatischen WLAN- oder Board-Neustarts.
- Tests für Authentifizierung, Wiederholschutz, Verbindungsabbrüche, Adresswechsel und den Erhalt der HA-Entitätskennungen.

## 1.1.0

- Neue optionale HA-Bedienseite für Laden, Einstellungen und Diagnose im responsiven Petrol-Design; lokale Ressourcen und unverändertes Projektlogo.
- Brückenstatus und WLAN-Einrichtung im gleichen Design. Statusaktualisierungen erhalten Tastaturfokus und geöffnete Bereiche.
- Getrennte Dashboard- und Brückenpakete. Das HACS-Update installiert die Dashboard-Ressource und die Board-Software nicht automatisch.
- Brücken-Installer mit Vorprüfung, Sicherung und automatischer Wiederherstellung bei Fehlern. Bestehende Konfiguration und USB-/TCP-Portzuordnungen bleiben erhalten. Keine Änderungen der Ladesteuerung oder automatischen Schreibzugriffe auf die Wallbox.
- Zusätzliche Frontend- und Installerprüfungen in der CI; bestehende Prüfungen bleiben aktiv.

## 1.0.3

- Logo-Adresse in der README für die Darstellung in HACS korrigiert.

## 1.0.2

- Petrol-Projektlogo für GitHub und die Home-Assistant-Integration ergänzt.

## 1.0.1

- Beim Setzen der festen Stromgrenze wird eine darüberliegende gespeicherte Ladestromvorgabe abgesenkt und der zurückgelesene Wert geprüft.
- HA-Entitäten zeigen die Ergebnisse aufeinanderfolgender Schreibaktionen ohne verzögerte Zwischenstände an.
