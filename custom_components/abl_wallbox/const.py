"""Integration constants and controller state labels."""

DOMAIN = "abl_wallbox"
DEFAULT_PORT = 5020
DEFAULT_INTERVAL = 15
PLATFORMS = ["sensor", "binary_sensor", "number", "switch", "select", "button"]
STATE_LABELS = {
    "A1": "Wartet auf Fahrzeug",
    "B1": "Fahrzeug wartet auf Freigabe",
    "B2": "Laden freigegeben",
    "C2": "Lädt",
    "C3": "Lädt mit reduzierter Leistung",
    "C4": "Lädt mit Begrenzung wegen Schieflast",
    "E0": "Gesperrt",
    "E1": "Produktionstest",
    "E2": "Konfigurationsmodus",
    "E3": "Bus inaktiv",
    "F1": "Schütz ungewollt geschlossen",
    "F2": "Interner Controllerfehler",
    "F3": "Gleichstromfehler erkannt",
    "F4": "Zeitüberschreitung der externen Steuerung",
    "F5": "Steckerverriegelung fehlgeschlagen",
    "F6": "Kabelerkennung außerhalb des Bereichs",
    "F7": "Fahrzeug fordert Belüftung an",
    "F8": "Control-Pilot-Signal außerhalb des Bereichs",
    "F9": "Überstrom erkannt",
    "F10": "Temperatur außerhalb des Bereichs",
    "F11": "Schütz ungewollt geöffnet",
    "F14": "Externe Steuerung nicht erreichbar",
    "F15": "Schieflast erkannt",
    "F16": "Interne Strommessung gestört",
    "F17": "Erhöhte Temperatur",
}
