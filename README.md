# Balter ERA IP für Home Assistant

Inoffizielle Home-Assistant-Integration für die Türsprechanlagen der Serie
Balter ERA IP. Sie ersetzt die veraltete Android-App „Balter ERA IP“ und spricht
dasselbe Cloud-Protokoll wie diese.

> Nicht von Balter. Benutzung auf eigenes Risiko.

## Funktionen

| Funktion | Entität (Beispiel) | Hinweis |
|---|---|---|
| Tür 1 öffnen | `button.home_tur_1_offnen` | dauert 4–6 s, siehe unten |
| Tür 2 öffnen | `button.home_tur_2_offnen` | |
| Kamera | `camera.home` | Standbild + Live-Video (Stream) |
| Kamerabild aktualisieren | `button.home_kamerabild_aktualisieren` | Diagnose-Knopf |
| Türklingel | `event.home_turklingel` | Ereignis `ring`, Quelle: Sensor am Innenmonitor (z. B. Shelly) |

Die Entitäts-IDs hängen vom Gerätenamen in der Balter-App ab (hier „Home“).

**Gut zu wissen**

- **Tür öffnen dauert 4–6 Sekunden.** Die Außenstation hängt am 2-Draht-Bus und
  wird erst eingeschaltet, wenn ein Videostream startet (ca. 3,5 s). Befehle
  davor ignoriert sie. Die Integration wartet deshalb auf das erste Videobild.
- **Jeder Kamerazugriff schaltet die Außenstation ein.** Das Standbild wird
  deshalb zwischengespeichert und nur beim Klingeln, über den Knopf
  „Kamerabild aktualisieren“ oder einmal nach dem Start neu geholt. Das
  Live-Video läuft nur, solange es jemand ansieht (siehe „Live-Video“).
- **Klingeln kommt nicht aus der Cloud.** Die Balter-Cloud hat im Test weder
  per Push noch über die Konto-Verbindung ein Klingeln gemeldet. Das Klingeln
  wird deshalb über einen Sensor am Innenmonitor erkannt (siehe unten).
- Alles läuft über die Balter-Cloud (Server in Deutschland), es braucht also
  Internet. Video und Steuerbefehle sind dort unverschlüsselt.

## Voraussetzungen

- Home Assistant 2025.2 oder neuer
- Ein Konto der Balter-ERA-IP-App, mit dem die Anlage verknüpft ist.
  **Empfohlen: ein eigenes Konto nur für Home Assistant** (in der App einen
  zweiten Benutzer anlegen und die Anlage für ihn freigeben). Mit dem
  Handy-Konto kann es passieren, dass sich App und Home Assistant gegenseitig
  abmelden.

## Installation

### Über HACS (empfohlen)

1. HACS → ⋮ (oben rechts) → **Benutzerdefinierte Repositories**
2. Repository `https://github.com/tehdaniel/Balther-era-ip`, Typ **Integration**, hinzufügen
3. „Balter ERA IP“ suchen, **Herunterladen**
4. Home Assistant neu starten

### Von Hand

1. Den Ordner `custom_components/balter_era` aus diesem Repository nach
   `config/custom_components/balter_era` kopieren (z. B. mit dem Samba- oder
   File-Editor-Add-on).
2. Home Assistant neu starten.

## Einrichtung

1. **Einstellungen → Geräte & Dienste → Integration hinzufügen → „Balter ERA IP“**
2. Benutzername (E-Mail) und Passwort des Balter-Kontos eingeben.
   Optional gleich den **Klingel-Sensor** (z. B. Shelly) auswählen.
   „Server“ und „Port“ unverändert lassen.
3. Es erscheint ein Gerät mit Kamera, Türklingel und den Knöpfen.

## Live-Video

Die Kamera liefert einen echten Videostream (H.264, 704×576). Die Integration
stellt ihn Home Assistant intern bereit; Home Assistant zeigt ihn über WebRTC
(go2rtc) bzw. HLS an – in der App, im Browser und in Dashboards.

- **Im Dashboard:** Karte „Bild-Entität“ (Picture Entity) mit `camera.home` und
  **Kameraansicht: Live**, oder einfach die Kamera antippen.
- **Verzögerung:** einige Sekunden bis zum ersten Bild, weil die Außenstation
  erst eingeschaltet wird (ca. 3,5 s), dazu die Pufferung von Home Assistant.
- **„Stream vorladen“** in den Kamera-Einstellungen **nicht** aktivieren: Sonst
  hält Home Assistant den Stream dauerhaft offen und die Außenstation bleibt an.

## Klingeln mit einem Shelly erkennen

Die Integration braucht für das Klingeln eine Entität, die beim Klingeln
auslöst. Geeignet sind:

| Entität | löst aus, wenn … |
|---|---|
| `binary_sensor`, `switch`, `input_boolean` | sie von „aus“ auf „an“ wechselt |
| `event` (z. B. Shelly-Eingang im Taster-Modus) | ein neues Ereignis kommt |

### Hardware

- Den Shelly an einen Ausgang des Innenmonitors anschließen, der beim Klingeln
  schaltet, z. B. einen Klingel- oder Alarmausgang. Welche Klemmen der Monitor
  dafür hat, steht in seiner Anleitung.
- Für Kleinspannungs- oder potentialfreie Signale eignet sich ein Shelly mit
  passenden Eingängen, z. B. ein **Shelly Plus Uni**. Shellys, deren Eingänge
  Netzspannung erwarten (z. B. Shelly i4), passen dafür nicht.
- **Nicht direkt am 2-Draht-Bus abgreifen** und den Monitor nicht mit
  Netzspannung verbinden. Bei Unsicherheit eine Elektrofachkraft fragen.
- Den Shelly mit der Shelly-Integration in Home Assistant einbinden und prüfen,
  welche Entität beim Klingeln umschaltet (Entwicklerwerkzeuge → Zustände).

### Sensor in der Integration auswählen

Entweder direkt bei der Einrichtung, oder später:

1. **Einstellungen → Geräte & Dienste → Balter ERA IP**
2. Beim Eintrag auf **Konfigurieren** (Zahnrad bzw. ⋮-Menü) klicken.
3. Bei **Klingel-Sensor** die Shelly-Entität auswählen, speichern.

Gibt es „Konfigurieren“ nicht, ist eine alte Version installiert (vor 0.3.0):
in HACS aktualisieren und Home Assistant neu starten.

Danach löst bei jedem Klingeln `event.home_turklingel` mit dem Ereignis `ring`
aus, und das Kamerabild wird automatisch aktualisiert. Mehrfaches Auslösen
innerhalb von 30 Sekunden zählt als ein Klingeln.

Die Option **„Zusätzlich auf die Balter-Cloud hören (experimentell)“** hält eine
Verbindung zur Balter-Cloud offen und registriert sich für deren
Push-Nachrichten. Sie hat im Test kein Klingeln geliefert und ist deshalb aus.

## Beispiel: Benachrichtigung mit Bild und „Tür öffnen“

Für die Home-Assistant-App auf dem Handy. `notify.mobile_app_dein_handy` und die
Entitäts-IDs an die eigenen anpassen.

```yaml
- alias: "Türklingel: Benachrichtigung mit Bild"
  triggers:
    - trigger: state
      entity_id: event.home_turklingel
      not_from: unavailable
  actions:
    # Das Bild wird nach dem Klingeln geholt (ca. 4 s)
    - delay: "00:00:06"
    - action: notify.mobile_app_dein_handy
      data:
        title: "Es klingelt"
        message: "Jemand steht vor der Tür."
        data:
          image: /api/camera_proxy/camera.home
          actions:
            - action: BALTER_TUER_2
              title: "Tür öffnen"

- alias: "Türklingel: Tür aus der Benachrichtigung öffnen"
  triggers:
    - trigger: event
      event_type: mobile_app_notification_action
      event_data:
        action: BALTER_TUER_2
  actions:
    - action: button.press
      target:
        entity_id: button.home_tur_2_offnen
```

## Fehlersuche

Ausführliches Protokoll in `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.balter_era: debug
```

| Problem | Lösung |
|---|---|
| „Benutzername oder Passwort falsch“ | Zugangsdaten in der Balter-App prüfen |
| Tür öffnet nicht | Log prüfen: erscheint `station awake, first video after …`? Ohne Videobild ist die Außenstation nicht erreichbar. |
| Kein Kamerabild | Knopf „Kamerabild aktualisieren“ drücken; `ffmpeg` muss in Home Assistant verfügbar sein (bei Home Assistant OS immer der Fall) |
| Kein Live-Video | Die Integrationen `stream` bzw. `go2rtc` müssen aktiv sein (Standard bei `default_config:`); Log auf Meldungen von `balter_era` und `stream` prüfen |
| Klingeln kommt nicht an | In den Optionen den richtigen Sensor wählen; prüfen, ob die Shelly-Entität beim Klingeln wirklich umschaltet |
| Handy-App wird abgemeldet | Ein eigenes Konto für Home Assistant verwenden |

### Ohne Home Assistant testen

Mit Python 3.12+ auf dem PC, im Ordner dieses Repositorys:

```bash
python tools/balter_cli.py dein@konto.de devices
python tools/balter_cli.py dein@konto.de open --door 2 -v
python tools/balter_cli.py dein@konto.de snapshot --out bild.jpg -v
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
