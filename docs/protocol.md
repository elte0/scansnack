# ScanSnap iX1500 – WLAN-Protokoll

Single Source of Truth für das Protokoll. Alles hier stammt aus Mitschnitten von ScanSnap Home
oder aus Live-Tests gegen das Gerät. Vermutungen stehen ausschließlich unter
[Offene Fragen](#offene-fragen) und sind im Code mit `# UNVERIFIED:` markiert.

Code: `src/scansnap/adapters/ix1500/`. Golden Tests: `tests/fixtures/captures/` (aus pcap extrahiert,
ohne Bildinhalte; Erzeugung mit `tools/make_fixtures.py`).

Testumgebung der Live-Befunde: Mac 192.168.178.37, Scanner 192.168.178.29, ScanSnap Home beendet.

## Überblick

| Kanal | Transport | Zweck | Modul |
|---|---|---|---|
| Discovery / Heartbeat | UDP 52217 (Scanner) | Suche, Keepalive | `discovery.py` |
| Steuerkanal | TCP 53219 | Registrierung, Benutzer/Profile (JSON), eine Verbindung pro Kommando | `control.py` |
| Scan-Kanal | TCP 53218 | SCSI-Tunnel, dauerhafte Verbindung | `scsi.py`, `scanner.py` |
| Scan-Taste | UDP `host_port` (Host, bei uns 55265) | Meldung „Scan“ am Touchpanel | `button.py` |
| Pairing-Meldungen | UDP 53220 (Host) | beim Koppeln beobachtet, **nicht ausgewertet** | – |

Kein TLS, keine Kryptografie.

## Framing (TCP)

Jeder TCP-Frame beginnt mit `u32 length (BE, inkl. Header) | b"ssNR"`.

Client → Scanner, 32-Byte-Header:

| Offset | Inhalt |
|---|---|
| 0 | u32 length |
| 4 | `b"ssNR"` |
| 8 | u32 command |
| 12 | u32 0 |
| 16 | 6 B MAC des Hosts |
| 22 | u16 0 |
| 24 | 4 B Zusatzfeld (Scan-Kanal: Host-IP, Steuerkanal: meist 0) |
| 28 | u32 0 |

Scanner → Client, 16-Byte-Header:

| Offset | Inhalt |
|---|---|
| 0 | u32 length |
| 4 | `b"ssNR"` |
| 8 | u32 0 |
| 12 | u32 status (Scan-Kanal: SCSI-Status, 2 = CHECK CONDITION) |

Nach dem Verbindungsaufbau sendet der Scanner einen 16-Byte-Greeting-Frame.

## Discovery und Heartbeat (UDP 52217)

Host-Probe, 32 Byte:

| Offset | Inhalt |
|---|---|
| 0 | 4 B Kennung (`VENS`, `ssNR`, `V2ss`) |
| 4 | u32 Sequenz (0; Heartbeat: 1) |
| 8 | 4 B Host-IP |
| 12 | 6 B Host-MAC |
| 18 | u32 0 |
| 22 | u16 UDP-Port des Hosts (Antwortziel) |
| 24 | u16 Flag (`VENS` 0x0010, `ssNR` 0x0100, `V2ss` 0x1000) |
| 26 | 6 B 0 |

Antwort des Scanners, 144 Byte:

| Offset | Inhalt |
|---|---|
| 0 | `b"ssNR"` |
| 16 | 4 B Scanner-IP |
| 20 | u32 Port Scan-Kanal (53218) |
| 24 | u32 Port Steuerkanal (53219) |
| 28 | 6 B Scanner-MAC |
| 40 | 64 B Gerätename, z. B. `iX1500-TEST000000` |
| 104 | 16 B Modell, z. B. `ScanSnap iX1500 ` |
| 124 | 8 B ID des aktuell gekoppelten Hosts |
| 132 | 12 B Seriennummer |

Beobachtet:
- ScanSnap Home schickt beim Start alle drei Probes von Port 55264 (Port-Feld 55264), 3× im Abstand 0,3 s.
- Discovery per Broadcast antwortet nicht immer → in der Praxis `--ip` verwenden.
- Unicast-Probes: beantwortet werden nur `ssNR`/0x0100/seq 0 und `ssNR`/0x1000/seq 1.
- Heartbeat von ScanSnap Home: `ssNR`, seq 1, Flag 0x1000 alle ~0,5 s, erst ab ~5 s nach App-Start.
  Der Scanner antwortet mit 12 Byte `ssNR 8001 0000 0000…` (oft mehrfach), aber unzuverlässig.

## Steuerkanal (TCP 53219)

Pro Kommando eine eigene TCP-Verbindung: Greeting lesen, Frame senden, Antwort lesen.

| Command | Bedeutung | Body / Antwort |
|---|---|---|
| 0x11 | Host registrieren | 352-Byte-Binärblock (s. u.); Antwort-Status an Offset 12 |
| 0x12 | Bedeutung unbekannt | ohne Body, Zusatzfeld = 1; antwortet nicht immer |
| 0x13 | Gerätename | Antwort ab Offset 16, 64 B, nullterminiert |
| 0x30 | Zustand | Antwort u32 an Offset 16 (Bedeutung unbekannt) |
| 0x41 | JSON-Aufruf | s. u. |

### 0x11 Registrierung (352 Byte)

| Offset | Inhalt |
|---|---|
| 0 | `01 00 1e 00` |
| 4 | u32 Flag: 5 = Koppeln (Pairing), 0 = Verbinden |
| 8 | u32 0 |
| 12 | 4 B Host-IP |
| 16 | u16 0 |
| 18 | u16 `host_port` |
| 68 | u16 Jahr, u8 Monat, Tag, Stunde, Minute, Sekunde, u8 0 (Ortszeit) |
| 76 | 8 B Host-ID |
| 84 | i32 Zeitzonen-Bias in Sekunden (−UTC-Offset) |

Byte-identisch zum Pairing-Capture (Test `test_register_body_matches_capture`).

### 0x41 JSON-Aufruf

Body:

| Offset | Inhalt |
|---|---|
| 0 | u32 BE Länge des restlichen Bodys |
| 4 | u16 BE Unterkommando |
| 6 | 10 B 0 |
| 16 | u32 LE JSON-Länge |
| 20 | JSON (ScanSnap Home: `indent=2`, Trenner `" : "`) |

Antwort = Server-Header + dasselbe Layout ab Offset 16. Ohne JSON ist es eine Quittung:
u32 Status an Offset 24 (0 = ok).

| Sub | Bedeutung |
|---|---|
| 0x0100 | Benutzer (`conn_user.users`, `conn_user.select`) und Profile lesen |
| 0x0110 | Benutzer/Profile schreiben |
| 0x0210 | Host ankündigen (`host_address`, `function_level` 2, `current_user_id`, `host_port`, `version` 1) |
| 0x0610 | Profil wählen (`{"version": 1, "prof_id": …}`) |
| 0x0810 | Scan-Einstellungen fürs Touchpanel |

Fehler-Frames: `ssNR fffffffe` statt Antwort (z. B. 0x0610 im falschen Zustand).

Quittung `0xffffffff` bei 0x0110, beobachtet bei:
- Benutzername > 30 Zeichen (30 ok, 31 nicht),
- Versuch, `select` umzustellen,
- umsortierter Benutzerliste,
- Benutzername `"scansnap-bridge (Mac.fritz.box)"`.

Weitere Status von 0x11 Flag 0: `fffffffc` (Scanner noch im Pairing-Zustand),
`fffffff9` (Host ist am Touchpanel nicht ausgewählt).

## Scan-Kanal (TCP 53218, SCSI-Tunnel)

Request: command = 1, Zusatzfeld = Host-IP. Body:

| Offset | Inhalt |
|---|---|
| 0 | u32 CDB-Länge (6/10/12) |
| 4 | u32 erwartete Datenlänge vom Scanner |
| 8 | u32 Länge der Daten zum Scanner – **Pflicht**: fehlt sie, lehnt der Scanner E9/D4 mit Sense 5/26/00 ab |
| 12 | 4 B 0 |
| 16 | 16 B CDB (mit Nullen aufgefüllt) |
| 32 | Daten zum Scanner (optional) |

Response: Server-Header (Status in Byte 12–15), 24 B Null, ab Byte 40 die Daten.
Status 2 = CHECK CONDITION → `03` Request Sense (18 B) holen.

### Init einer neuen Verbindung (wie ScanSnap Home)

`12` INQUIRY (0x60 B), `12 01 f0` INQUIRY VPD f0 (0x90 B), `E7` (0x0C B), `C2`.

### C2 Hardware-Status (0x30 B), alle 250 ms

ScanSnap Home hält 53218 dauerhaft offen und fragt C2 alle 250 ms ab; während eines Scans kein C2.

| Daten-Offset | Inhalt |
|---|---|
| 3, Bit 7 | Einzug leer |
| 4, Bit 0 | Scan-Taste gedrückt (~0,3 s Puls) |
| 16 | Statusbyte: 0x80 vor 0x11, danach 0xe0; nach 0x11 Flag 5 (Pairing) 0xe1 |
| 32–39 | aktuell ausgewählter Host (Host-ID) |

### Scan-Sequenz

```
D5 00 00 01 08 08   Session öffnen      (8 B raus, 8 B rein)
D8                  Start
E9                  Fenster setzen      (32 B: dpi x/y, Breite/Höhe in 1/1200 Zoll, 0x05)
D4                  Modus setzen        (80 B: Farbmodus, dpi, Qualität)
03                  Request Sense
pro Blatt:
  E0                Blatt einziehen     → CHECK CONDITION + Sense 3/80/03 = Einzug leer
  28 00 00 02 …     JPEG-Chunks, Vorder-/Rückseite abwechselnd
                    CHECK CONDITION + Sense EOM (Info = Restlänge) = Seite fertig
  28 00 80 …        Bildinfo (0x20 B: Breite, Höhe max, Breite, Höhe, dpi x, dpi y)
D6                  Session freigeben (auch nach Fehlern)
```

- Read-CDB: `28 00 00 02 00 <80 bei Rückseite> <Länge 3 B> 00 <Seite> <Chunk>`; die ersten 2 Datenbytes
  jedes Chunks sind Seite und Chunk-Nummer.
- Bilder kommen als fertige JPEGs (YCbCr, auch im Graumodus 3 Kanäle; JFIF-dpi = 0).
- Der Scanner liest immer beide Seiten; Simplex = Rückseite verwerfen.
- Seiten kommen um 180° gedreht und mit dem ganzen Scanfeld (Breite 2608 px @ 300 dpi, Länge mit
  ~50/110 px Hintergrund). Hintergrund ~235–240, Papier ~252, Schattenkante ~225 (Live-Scans).
- Scanfeld wie von ScanSnap Home gesendet: Breite 0x28D0, Höhe 0x44DC (1/1200 Zoll).

Parameterblöcke E9/D4 sind für gray/150 und color/600 byte-identisch mit den Captures
(Read-Länge 0x300000 bzw. 0x20000). Für Profil …0010 (color/300) sendet ScanSnap Home ein D4,
das in Byte 10 (0x90), 13 (0x00) und 40 (0x0d) von unserem Template abweicht; unser color/300-Block wird
trotzdem akzeptiert. Andere Kombinationen sind abgeleitet (siehe Offene Fragen).

### Sense-Codes

| Sense | Bedeutung | Quelle |
|---|---|---|
| EOM-Bit, Info = Restlänge | Seitenende | Capture |
| 3/80/01 | Papierstau / nicht einziehbar | live (zerknülltes Blatt, simulierter Stau) |
| 3/80/03 | Einzug leer | Capture, live |
| 3/80/33 | D5 ohne vorheriges 0x0610 („WLAN-Steuerfehler“) | live |
| 5/26/00 | E9/D4 ohne Out-Länge im SCSI-Header | live |

Weitere 3/80/xx-Texte in `scsi.MEDIUM_ERRORS` siehe Offene Fragen.

## Scan-Taste am Touchpanel

Druck auf „Scan“ → 48-Byte-UDP an `host_port` des ausgewählten Hosts (bei uns 55265), 3× im Abstand 0,5 s
(`captures/panel_scan1.pcap`, `panel_scan2.pcap`):

| Offset | Inhalt |
|---|---|
| 0 | u32 Länge (0x30) |
| 4 | `b"ssNR"` |
| 8 | u32 1 |
| 12 | u32 0 |
| 16 | u8 Ereigniszähler (je Druck +1, Wiederholungen gleich) |
| 20 | 4 B Scanner-IP |
| 24 | 6 B Scanner-MAC |

Keine Antwort nötig: das Panel zeigt „Es wird auf eine Antwort vom Gerät gewartet“, bis der Host normal
0x0100 → 0x0610 → D5 … ausführt. Ohne laufende Sitzung (Scan-Kanal + C2-Polling) kommt nichts an.
Live bestätigt mit `scansnap listen` (2 Drücke, je 1 Blatt).

## Abläufe

### App-Start / Verbinden (Kalt-Capture `captures/cold_start.pcap`)

1. UDP-Probes (s. o.).
2. 53218 Verbindung A: INQUIRY, VPD f0, E7, dann C2-Polling.
3. 53219: 0x13 → 0x0100 → 0x0210 → 0x11 mit **Flag 0**. Kein 0x30/0x12.
4. Verbindung A schließen, **neue Verbindung B** auf 53218 (nur C2-Polling, kein INQUIRY).
5. UDP-Heartbeat ab ~5 s; 0x0210 wiederholt sich etwa alle 10 s.

Live bestätigt (`captures/keepalive1.log`): Sitzung 12 s gehalten, danach zweimal Scan auf derselben
Verbindung; bis „Einzug leer“ 2,6 s statt 4,9 s mit Neuverbindung.

### Scan

0x0100 (get_users) → 0x0610 (Profil wählen) → D5 … auf dem Scan-Kanal, Abstand ~100–150 ms.
C2-Polling vor D5 anhalten, nach D6 wieder aufnehmen.

- D5 ohne vorheriges 0x0610 → Sense 3/80/33.
- Live-Scan funktioniert (`scan11.log`): gültiges JPEG 2608×3688 RGB, color/300.
  Ausschlaggebend waren der Verbindungsablauf oben und die Out-Länge im SCSI-Header.

### Koppeln (Pairing-Capture, `tests/fixtures/captures/01_pairing.json`)

0x13 → 0x0100 → 0x30 → 0x12 → 0x11 (Flag 5) → 0x0210.

### Eigenen Benutzer + Profil anlegen (`claim`, `claim9.log`, `scan21.log`)

1. Sitzung mit Pairing-Ablauf (0x11 Flag 5, C2-Status 0xe1).
2. 0x0110 mit vollständiger Benutzerliste (eigener Eintrag an seiner Stelle, sonst `0xffffffff`),
   unverändertem `select` und allen Profilen plus eigenem Profil (Vorlage `profile_template.json`).
3. 0x0210.
4. Pairing abschließen: 0x30 → 0x12 → 0x11 Flag 0. Ohne Abschluss bleibt C2 0xe1,
   0x11/0 → `fffffffc`, 0x0610 → `fffffffe`.

Danach am Touchpanel den Benutzer (`scansnap-bridge`) wählen – `select` (= C2 Byte 32–39) lässt sich
nur am Panel umschalten. Dann scannt die eigene Identität.

## Zustände

| Zustand | Erkennbar an | Erlaubt | Abgelehnt |
|---|---|---|---|
| nicht registriert | C2 16 = 0x80 | Discovery, 0x13, 0x0100, 0x30, 0x0210 | – |
| verbunden (0x11 Flag 0) | C2 16 = 0xe0 | 0x0610, Scan | 0x0110 (`fffffffe`) |
| Pairing (0x11 Flag 5) | C2 16 = 0xe1 | 0x0110 | 0x0610 (`fffffffe`), 0x11/0 (`fffffffc`) |
| nicht ausgewählt | C2 32–39 ≠ eigene Host-ID | – | 0x11 Flag 0 (`fffffff9`) |

Nach Benutzerwechsel am Panel liefert 0x0100 nur die Profile des gewählten Benutzers (anfangs leer).

## Historische Befunde

- 0x0610 → `fffffffe` auch mit exakter ScanSnap-Home-Identität und byte-identischem Frame ⇒ Ursache war
  der Zustand (Verbindungsablauf), nicht die Identität.
- Nur Scan-Kanal vor 0x0610 oder nur Heartbeat → weiterhin `fffffffe`.

## Offene Fragen

Jeder Punkt entspricht einem `# UNVERIFIED:` im Code (Fundstelle in Klammern).

1. **Parameterblöcke außerhalb gray/150 und color/600** (`scanner.mode_block`, `scanner.read_len`):
   abgeleitet; Read-Länge 0x20000 für alles ohne Capture übernommen. Live akzeptiert: color/300, gray/150.
2. **Sense-Texte 3/80/xx** (`scsi.MEDIUM_ERRORS`): aus dem Gedächtnis nach SANE `fujitsu.c`, nicht
   nachgeschlagen. Bestätigt sind nur 01, 03, 33.
3. **0x12 und 0x30** (`control.cmd12`, `control.state`): Bedeutung unbekannt, nur in der Reihenfolge
   von ScanSnap Home nachgebaut.
4. **E7 und INQUIRY VPD f0** (`scsi.open_session`): Inhalt nicht ausgewertet.
5. **Wartezeiten im Verbindungsablauf** (`device.Session.open`: 0,2 s vor Kanal B, 1,0 s danach):
   nicht einzeln getestet, ob nötig.
6. **Heartbeat nötig?** (`device.Session.open`): Antworten kommen unzuverlässig; ob der Scanner ohne
   Heartbeat die Sitzung beendet, ist nicht getestet.
7. **Welche der beiden Korrekturen war nötig** – Verbindungsablauf allein oder zusätzlich die Out-Länge?
   Nicht getrennt getestet (beides ist umgesetzt).
8. **Profile verschwinden** (`device.Device.ensure_profile`): gelegentlich auch ohne Benutzerwechsel,
   Ursache unklar. Vermutung (Nutzer): Profile werden pro Benutzer gespeichert, die von ScanSnap Home
   sind also noch da. Ungeprüft; Sicherung: `captures/users_backup_2026-10-29.json`.
9. **Wie lange wartet das Panel** nach „Scan“ auf den Host? (`button.py`)
10. **UDP 53220**: Benachrichtigungen des Scanners beim Pairing, Inhalt nicht ausgewertet.
11. **Graustufen** (`escl`): bei gray-D4 sieht das Bild grau aus, das JPEG hat aber 3 Kanäle. Ob der
    Scanner echte 1-Kanal-JPEGs liefern kann, ist offen.
12. **Einzugskapazität 50 Blatt** in den eSCL-Capabilities (`escl.server.capabilities_xml`): Herkunft
    nicht dokumentiert, nicht vom Gerät gelesen.
