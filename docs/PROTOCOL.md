# Balter ERA IP – Protokoll

Rekonstruiert aus der Android-App „Balter ERA IP“ 1.0.02.30 (Paket
`com.balter.bell.balterera`) und Mitschnitten mit PCAPdroid. Die App basiert
auf einem generischen OEM-SDK (`com.ddclient.dongsdk`, native Bibliothek
`libMobClientSDK.so`). Die Funktionsnamen unten stammen aus deren
Symboltabelle.

Alles läuft unverschlüsselt über die Balter-Cloud (`159.69.151.111`, Hetzner).
Die Adresse ist in der App fest hinterlegt, es gibt keine DNS-Abfrage.

## Rahmen

```
02 02 <u16 Länge> <Payload>        Nachricht (Länge ohne die 4 Byte Präfix)
02 01                              Keepalive (Konto-Kanal, alle 10 s, Server antwortet gleich)
```

Payload-Byte 0 ist der Typ: `02` = Steuerung, `01` = Medien (H.264/Audio).
Steuer-Payloads beginnen mit einem 45-Byte-Header, alle Zahlen sind Big-Endian:

| Offset | Konto-Kanal (`d6`) | Video-Sitzung (`ca`) |
|---|---|---|
| 0 | `02` | `02` |
| 1 | `d6` | `ca` |
| 2–3 | Befehl | Befehl |
| 4–7 | 0 | `04 00 02 00` (Client) / `00 00 05 00` (Ereignis vom Server) |
| 9–12 | 0 | Absender: Sitzungs-ID (Client) bzw. Geräte-ID (Server) |
| 13–16 | 0 | Empfänger: Geräte-ID (Client) bzw. Sitzungs-ID (Server) |
| 17–20 | 0 | 0, bei Status-Ereignissen der Stream-Typ |
| 21–24 | `00 01 00 01` | `00 01 00 01` |
| 27–28 | 0 | zufällige Verbindungs-ID des Clients |
| 29–32 | 0 | Benutzer-ID aus dem Konto-Login |
| 42 | `01` (Client→Server) | 0 |

## Konto-Kanal (TCP 3478)

| Befehl | Richtung | Inhalt |
|---|---|---|
| `0x0030` | → | Challenge anfordern (leerer Body) |
| `0x0031` | ← | 16 Zeichen Challenge |
| `0x0020` | → | Login, siehe unten |
| `0x0021` | ← | Login OK: `u32 user_id, u32, u32, u32 login_session, …, Push-Token` |
| `0x000d` | → | Geräteliste anfordern: `u32 user_id` |
| `0x000e` | ← | `u32 user_id, u32, u32 anzahl`, je Gerät `u32 device_id, u32, u32, 20 Byte Seriennummer, varstr Name, …, Firmware` |
| `0x0027` | → | Verbindung anfragen: `u32 0, u8 1, u32 user_id, u32 login_session, u32 device_id, 19×00` |
| `0x0028` | ← | u. a. öffentliche/LAN-Adresse des Geräts; am Ende `IPv4 relay, u16 port, u16 port, 6 Byte, 16 Byte Token, 16 Byte Geheimnis` |

Login-Body (`CDServerSend::SendCmd_Login`):

```
varstr username (max. 31 Zeichen)
16 Byte digest = MD5(username + MD5(passwort) + challenge)   (CalcAuthDigist)
u32 0, u32 0, u8 2 (PushOpr), u32 bekannte user_id (0 beim ersten Mal),
u32 0x03000001, u8 pushOS, u8 Sprache, varstr Push-Token, u32 1, u8 0
```

Ein Konto kann offenbar nur an einer Stelle angemeldet sein (`onLoginOtherPlace`).
Für Home Assistant ist deshalb ein eigenes Konto empfehlenswert.

## Video-Sitzung (TCP oder UDP 5439 am Relay aus `0x0028`)

Die Geräte-ID im Header wird aus der 20-stelligen Seriennummer berechnet
(`CamSNToID`): Zeichen an den Positionen 2,5,3,6,7,4,1 werden als Base-36-Ziffer
gelesen, zu jeder Ziffer kommt ein fester Versatz (12,34,10,21,30,25,4, modulo 36),
das Ergebnis wird als Base-36-Zahl zusammengesetzt. Passt es nicht in 32 Bit, ist die ID 0.

| Befehl | Richtung | Inhalt |
|---|---|---|
| `0x0001` | → | `pad32(token), u8 8, u32 0x17, u32 0, u32 geräte_id, u32 0x12` |
| `0x0002` | ← | 16 Zeichen Challenge |
| `0x0003` | → | `MD5(MD5(pad32(token)) + MD5(pad32(geheimnis)) + challenge)` |
| `0x0004` | ← | `u32 sitzungs_id` (ab jetzt im Header) |
| `0x0011` | → | Stream starten: `u32 geräte_id, u32 0x12, u32 0, u8 typ, 23×00` – `0x16` Video, `0x14` Ton, `0x02` Gegensprechen |
| `0x0012` | → | Streams stoppen: `u32 geräte_id, u32 0` |
| `0x0021` | ← | Status (Header-Offset 17 = Stream-Typ); Body-Status `01` = Gespräch von der Anlage beendet |
| `0x0022` | ← | Stream gestoppt |
| `0x007a` | → | **Tür öffnen**: `u32 geräte_id, u32 tür` – Tür 1 = `1`, Tür 2 = `101` |
| `0x008a` | ← | Tür-Befehl bestätigt |

**Timing beim Tür öffnen:** Die Außenstation hängt am 2-Draht-Bus und wird erst
durch den Stream-Start eingeschaltet; das erste Videobild kommt nach ca. 3,5 s.
Ein früher gesendetes `0x007a` wird vom Relay mit `0x008a` bestätigt, von der
Station aber ignoriert. Deshalb erst auf das erste Medien-Frame warten.

Über UDP stecken dieselben Nachrichten in einer eigenen Hülle
(`02 07 <seq> <ids> 84 …`), die hier noch nicht umgesetzt ist.

## Medien-Frames

Payload-Typ `01`, 37 Byte Kopf, danach die Nutzdaten:

| Offset | Inhalt |
|---|---|
| 0 | `01` |
| 4–5 | Laufnummer |
| 14–17 | Geräte-ID |
| 18 | Art: `0x91` Video-Schlüsselbild, `0x21` Audio (33 Byte) |
| 24–27 | Unix-Zeitstempel |
| 28–31 | Millisekunden-Zähler |
| 33–36 | Länge der Nutzdaten |

Video ist H.264 Baseline, 704×576, Annex-B (`00 00 00 01`). Das erste Bild nach
dem Stream-Start ist ein Schlüsselbild mit SPS, PPS, SEI und IDR.

## Klingeln

Es gibt zwei Wege:

**1. Konto-Kanal (solange eine Sitzung offen ist).** Der Server schickt
`0x0026` (auch unaufgefordert); der Client muss jede mit `0x0103` (leerer Body,
`SendCmd_GetDeviceStatusRepACK`) bestätigen. Body:

```
u32 user_id, u32 login_session, u32 anzahl, anzahl × (u32 device_id, u32 status), …
```

Status 6 = online, 7 = offline, **jeder andere Wert ist ein Alarm/Anruf**
(`CDServerRecv::OnGetDeviceStatus` → `CDServer::OnAlarm` → App `onCall`). Die App
fragt den Status nach dem Login mit `0x0025` (`u32 user_id, u32 login_session,
u32 anzahl, anzahl × u32 device_id`) an und hält die Sitzung mit `02 01` alle
10 s offen.

**2. Firebase-Push** (Projekt `bell-vision-project`, Absender-ID `208250547295`),
wenn die App nicht verbunden ist.

Token hinterlegen: Befehl `0x004d` (`SendCmd_SetPushInfoEx`) auf dem Konto-Kanal:

```
u8 Operation (0 löschen, 1 FORCE_ADD), u32 user_id, u32 0x03000001,
u8 Sprache (2 = Englisch), u32 2, u8 1, u8 pushOS (13 = Google), varstr Token,
u8 0, u8 altes pushOS, varstr altes Token
```

Der Server bestätigt mit `0x004e` (Echo der Push-Daten). Ob und mit welchem
Inhalt Klingel-Pushes an einen als Web-Push registrierten Empfänger gehen, ist
noch offen: im ersten Test kam beim Klingeln nichts an.

## Offen

- Statuswert beim Klingeln bestätigen; Firebase-Push an Web-Push-Empfänger klären.
- Audio (33-Byte-Frames, Codec unbekannt) und Gegensprechen.
- Direktverbindung im LAN (Monitore lauschen auf TCP/UDP 14622).
