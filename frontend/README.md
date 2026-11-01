# ABL-Wallbox-Bedienseite

Optionale Dashboard-Karte für die bestehende Integration. Läuft vollständig lokal, ohne zusätzliche Karten, Bibliotheken oder externe Schriftanbieter. Die normalen Home-Assistant-Geräteansichten bleiben verfügbar.

## Lokal einrichten

1. Das Dashboard-ZIP aus dem passenden GitHub-Release herunterladen. Den Ordner `abl-wallbox` nach `/config/www/` kopieren. Alternativ die Dateien `abl-wallbox-card.js`, `abl-design.css`, `icon.png` und `LICENSE` aus diesem Quellordner nach `/config/www/abl-wallbox/` kopieren. Das vorhandene Logo wird unverändert verwendet.
2. Unter **Einstellungen → Dashboards → Ressourcen** die Adresse `/local/abl-wallbox/abl-wallbox-card.js?v=1.1.0` als **JavaScript-Modul** eintragen. Dafür gegebenenfalls den erweiterten Modus im Benutzerprofil aktivieren. Bei einem späteren Update die vorhandene Ressource aktualisieren und den Versionszusatz anpassen, statt eine zweite Ressource anzulegen.
3. Ein eigenes Dashboard erstellen. In dessen Rohkonfiguration den Inhalt von [`dashboard.yaml`](dashboard.yaml) übernehmen, oder eine einzelne manuelle Karte mit `type: custom:abl-wallbox-card` ergänzen. Vorhandene Dashboard-Konfiguration nicht überschreiben.
4. Bei genau einer ABL-Integration wird die Wallbox automatisch gewählt. Bei mehreren Einträgen zeigt die Karte eine Auswahl. Optional kann `config_entry_id` die Karte fest an einen Eintrag binden.

Die Zuordnung erfolgt anhand der vorhandenen eindeutigen Entitätskennungen, nicht anhand veränderbarer Anzeigenamen. Umbenannte Entitäten bleiben damit nutzbar. Für die Einrichtung muss das Benutzerkonto das Entitätsregister lesen dürfen. Einträge mit deaktivierten Entitäten erscheinen als nicht verfügbar; sie werden nicht automatisch aktiviert.

## Bedienung

- **Übersicht:** Fahrzeugstatus, Ladezustand, geschätzte Leistung, Stromwerte, aktuelle Störungen, Ladestromvorgabe und Ladefreigabe.
- **Einstellungen:** feste Stromgrenze, Betriebsart, Schieflasterkennung, Busadresse, Fallback und Freigabe nach Neustart. Änderungen erfolgen erst mit der jeweiligen Speichern-Schaltfläche.
- **Diagnose:** Controllerdaten, Freigabeeingänge, alle 17 Fehlerzähler, in HA beobachteter Fehlerverlauf und manuelle Aktualisierung. Unter **Wartung** liegen Bus-Suche, bestätigter Neustart und ausdrücklich bestätigtes Löschen der Fehlerzähler.

Die Karte verwendet ausschließlich bestehende HA-Entitäten und Aktionen. Berechtigungen, controllerseitige Prüfungen und Konfigurationsbedingungen bleiben in der Integration wirksam. Fehlende Werte werden nicht als Null oder als Erfolg dargestellt. Eingaben und aufgeklappte Bereiche bleiben bei Statusaktualisierungen erhalten. Feste Einstellungen erfordern weiterhin ein getrenntes Fahrzeug. Die feste Stromgrenze wird vor dem Speichern zusätzlich bestätigt.

## Gestaltung und Pflege

Farben, Schrift, Abstände und Grundelemente stehen zentral in [`abl-design.css`](abl-design.css). Die Gestaltung ist auf die Karte begrenzt und überschreibt nicht das allgemeine Home-Assistant-Design. Die Spalten wechseln unter 700 Pixeln auf eine Spalte. Das originale PNG wird lokal geladen; es enthält keine neue Grafik oder veränderte Farben.

Die Tinker-Board-Oberflächen verwenden eine identische Kopie dieses Stylesheets unter `bridge/ui/abl-design.css`. Die Brücke und das WLAN-Portal benötigen ihr vollständiges `ui`-Verzeichnis; siehe die [Brücken-Anleitung](../bridge/README.md). Diese Dashboard-Dateien müssen separat kopiert werden; das HACS-Integrationspaket registriert keine Dashboard-Ressource automatisch. Das Dashboard-ZIP enthält außerdem die Beispielkonfiguration und diese Anleitung.

Lokale Logiktests ohne Zugriff auf eine echte Wallbox:

```sh
node --test tests/frontend.test.cjs
```

© 2026 Christians8710. [MIT-Lizenz](../LICENSE).
