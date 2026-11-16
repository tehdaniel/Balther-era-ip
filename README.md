# Balter ERA IP für Home Assistant

Inoffizielle Home-Assistant-Integration für die Türsprechanlagen der Serie
Balter ERA IP. Sie spricht dasselbe Cloud-Protokoll wie die (veraltete)
Android-App „Balter ERA IP“.

> Nicht von Balter. Benutzung auf eigenes Risiko.

## Stand

| Funktion | Entität | Status |
|---|---|---|
| Anmeldung mit App-Konto | – | ✅ am echten Gerät getestet |
| Tür 1 / Tür 2 öffnen | `button.…_tur_1_offnen`, `button.…_tur_2_offnen` | ✅ Tür 2 am echten Gerät getestet |
| Klingeln | `event.…_turklingel` (Ereignis `ring`) | neu, am echten Gerät noch ungetestet |
| Kamerabild | `camera.…` (Standbild + MJPEG-Live-Bild) | neu, am echten Gerät noch ungetestet |
| Kamerabild aktualisieren | `button.…_kamerabild_aktualisieren` | neu |
| Betrieb rein im LAN | – | offen |

Jeder Zugriff auf die Kamera schaltet die Außenstation ein (ca. 3,5 s bis zum
ersten Bild). Das Standbild wird deshalb zwischengespeichert und nur beim
Klingeln, über den Aktualisieren-Knopf oder einmalig beim Start neu geholt.
Das Live-Bild läuft, solange es in Home Assistant geöffnet ist.

Klingel-Meldungen kommen wie in der App als Firebase-Push. Home Assistant
registriert dafür ein eigenes Push-Token beim Balter-Konto.

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
python tools/balter_cli.py dein@konto.de snapshot --out bild.jpg -v
pip install firebase-messaging
python tools/balter_cli.py dein@konto.de listen -v
```

`snapshot` braucht `ffmpeg` im PATH für JPEG (sonst wird die rohe `.h264` gespeichert).

## Entwicklung

Protokoll und Client (ohne Home Assistant):

```bash
PYTHONPATH=custom_components/balter_era python -m unittest discover -s tests -t .
```

Integration (mit `pip install pytest-homeassistant-custom-component`):

```bash
pytest tests_ha
```

Das Protokoll ist in [docs/PROTOCOL.md](docs/PROTOCOL.md) beschrieben.
