# ABL-Netzwerkbrücke · Benutzeroberflächen

Statusseite und die drei WLAN-Einrichtungsansichten im gemeinsamen ABL-Design. Zur Installation das vollständige [Brücken-Update](../README.md) verwenden. Dieses Paket enthält kein SD-Karten-Abbild.

## Dateien und Einbindung

- `abl-bridge.py` nutzt dieses `ui`-Verzeichnis für Statusvorlage, Stylesheet, unverändertes Projektlogo und Lizenz.
- `evcc-comitup-web.py` verwendet die Vorlagen in `ui/portal` anstelle der mitgelieferten Comitup-Vorlagen. Die ursprünglichen Funk-, D-Bus-, Verbindungs- und Zugangsdatenfunktionen bleiben zuständig.
- Im Quellverzeichnis liegt `ui` neben den beiden Programmen. Der mitgelieferte Installer übernimmt die Installation nach `/usr/local/share/abl-bridge/ui/`, sichert die vorhandenen betroffenen Dateien und erhält ihre Zugriffsrechte.
- Ein Neustart des Brückendienstes unterbricht die TCP-Verbindung kurz. Der Installer stellt bei einem fehlgeschlagenen Update die bisherigen Programm- und UI-Dateien wieder her.
- Ein späteres SD-Abbild benötigt diese UI-Dateien sowie erneute System- und Bootprüfungen. Vorhandene veröffentlichte Abbilder werden durch dieses Update nicht verändert.

`abl-design.css` ist identisch mit der zentralen Gestaltung in `frontend/abl-design.css` im Repository. Keine externen Schriften, Icons, Skripte oder zusätzlichen Laufzeitpakete. Das vorhandene Logo bleibt unverändert.

## Erhaltene Schutzmechanismen

Der Board-Neustart erfordert weiter die ausdrückliche Bestätigung, gültiges JSON, denselben Ursprung, einen gültigen Host und ein CSRF-Token. Doppelte Anfragen planen nur einen Neustart. GET-Anfragen bleiben ohne Geräte-Schreibaktion. Das Token erscheint nicht im JSON-Status. Die WLAN-Formulare verwenden dieselben Routen und Feldnamen wie Comitup; Passwörter erscheinen weder in der Verbindungsansicht noch in URLs. Das Passwortfeld wird erst auf ausdrücklichen Wunsch sichtbar.

## Lizenzen

Neue ABL-Vorlagen, Gestaltung und Grafikeinbindung: © 2026 Christians8710, [MIT](LICENSE). Der bestehende WLAN-Dienst **Comitup** bleibt unter GPL-2.0 oder neuer; seine bestehenden Copyright- und Lizenzhinweise im Paket bleiben erhalten. Die eigenen Vorlagen ersetzen keine Lizenzdateien oder Hinweise der installierten Drittanbieterpakete.
