# Balter ERA IP für Home Assistant

Inoffizielle Home-Assistant-Integration für die Türsprechanlagen der Serie
Balter ERA IP. Sie spricht dasselbe Cloud-Protokoll wie die (veraltete)
Android-App „Balter ERA IP“.

> Nicht von Balter. Benutzung auf eigenes Risiko.

## Stand

| Funktion | Status |
|---|---|
| Anmeldung mit App-Konto | ✅ |
| Tür 1 / Tür 2 öffnen (Buttons) | ✅ im Test gegen nachgebaute Server-Antworten, am echten Gerät noch ungetestet |
| Klingel-Ereignis | geplant (Firebase-Push) |
| Kamerabild | geplant (H.264 aus dem Video-Stream) |
| Betrieb rein im LAN | offen |

## Installation

1. Ordner `custom_components/balter_era` nach `config/custom_components/` kopieren
   (oder dieses Repository in HACS als benutzerdefiniertes Repository hinzufügen).
2. Home Assistant neu starten.
3. *Einstellungen → Geräte & Dienste → Integration hinzufügen → Balter ERA IP*.

Für Home Assistant am besten ein **eigenes App-Konto** anlegen und die Anlage
dafür freigeben. Ein Konto kann nur an einer Stelle angemeldet sein, sonst wird
die Handy-App abgemeldet.

## Ohne Home Assistant testen

```bash
python tools/balter_cli.py dein@konto.de devices
python tools/balter_cli.py dein@konto.de open --door 2 -v
```

## Entwicklung

```bash
PYTHONPATH=custom_components/balter_era python -m unittest discover -s tests -t .
```

Das Protokoll ist in [docs/PROTOCOL.md](docs/PROTOCOL.md) beschrieben.
