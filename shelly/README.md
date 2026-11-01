# Shelly Pro mit RS485 Add-on

Die ABL verwendet Modbus ASCII mit `>` als Antwortanfang. Der native Shelly-Modbus-RTU-Client passt dazu nicht. Das mitgelieferte Skript überträgt die ASCII-Telegramme stattdessen über die offiziellen Shelly-UART- und HTTPServer-APIs.

Voraussetzungen: kompatibles Shelly-Pro-Gerät mit RS485 Add-on und eine stabile Firmware, die `Serial`, `UART` und `HTTPServer` bereitstellt. Geräte ohne diese APIs werden durch das Skript nicht nachgerüstet.

1. Das Add-on nach Shelly-Anleitung aktivieren. Die `Serial`-Instanz des RS485-Ports auf `jsuart` stellen. Die Kennung ist üblicherweise 0. Die tatsächlich unterstützten Modi stehen im Attribut `modes` der Serial-Komponente; `Serial.GetConfig` liefert die aktuelle Konfiguration.
2. `abl-uart-bridge.js` als neues Shelly-Skript speichern und starten. Automatischen Start bei Bedarf in der Shelly-Oberfläche einschalten. Das Skript verwendet UART 0 mit 38400 Baud und 8E1; Half-Duplex- und DE-Einstellungen müssen zum Add-on passen.
3. In der HA-Integration **Shelly Pro RS485 (UART-Skript)** wählen und Host, HTTP-Port (normalerweise 80), Skript-ID, Busadresse und gegebenenfalls den vorhandenen Shelly-Benutzer `admin` mit Passwort eintragen.

Der Endpunkt lautet `/script/<Skript-ID>/abl` und übernimmt die vorhandene HTTP-Authentifizierung des Shelly. Es wird kein MQTT benötigt. Die HA-Verbindungsprüfung schreibt keine Controller-Einstellungen. Das Skript installiert oder konfiguriert die HA-Integration nicht selbst.

Der Transport und die Skriptlogik werden mit einem lokalen HTTP-Testserver und simulierten Shelly-UART-APIs geprüft. Ein physischer Shelly mit diesem Add-on gehört bislang nicht zu den live getesteten Geräten. Die Live-Prüfung der Wallbox erfolgt über die Tinker-Board-TCP-Brücke.

Referenzen:

- [Shelly Serial](https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Serial/)
- [Shelly UART](https://shelly-api-docs.shelly.cloud/gen2/Scripts/APIs/UART/)
- [Shelly HTTPServer](https://shelly-api-docs.shelly.cloud/gen2/Scripts/APIs/HTTPServer/)
