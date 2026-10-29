# scansnap-bridge – Kontext für Claude Code

## Ziel
ScanSnap iX1500 per WLAN im macOS-Scanfenster nutzbar machen. Weg: eigener Client für das
proprietäre WLAN-Protokoll (dieses Repo), danach eine eSCL/AirScan-Bridge (HTTP + Bonjour
`_uscan._tcp`), später als Docker-Container mit Host-Networking auf dem Server.

## Struktur
- `src/scansnap/wire.py` – Framing (`u32 len | "ssNR"`), Debug-Trace (`--debug` / `SCANSNAP_DEBUG=1`)
- `discovery.py` – UDP 52217, Probe/Antwort-Layout im Docstring, `Heartbeat`
- `control.py` – Steuerkanal TCP 53219, eine Verbindung pro Kommando; 0x11 Registrierung,
  0x13 Name, 0x30 Zustand, 0x41 JSON (Sub 0x0100 Benutzer/Profile, 0x0110 Profile schreiben,
  0x0210 Host ankündigen, 0x0610 Profil wählen, 0x0810 Scan-Einstellungen fürs Touchpanel)
- `scsi.py` – Scan-Kanal TCP 53218, SCSI-Tunnel, dauerhafte Verbindung, C2-Polling
- `scanner.py` – Scan-Sequenz D5, D8, E9, D4, 03, (E0, 28-Reads, 28-Info)*, D6
- `device.py` – `Session`/`session()` (Verbindungsablauf), `Device.scan()` (Auftrag inkl. Profilwahl, Auto-`claim`);
  `Device(keep_alive=True)` hält die Sitzung offen (C2-Polling, Heartbeat, 0x0210 alle 10 s), baut sie bei
  Abriss einmal neu auf. Die App nutzt das und verbindet sich schon beim Start.
- `escl.py` – eSCL-Server (ScannerCapabilities/Status, ScanJobs, NextDocument), nur ADF, JPEG/PDF
- `pdf.py` – JPEG→PDF ohne Abhängigkeiten; `bonjour.py` – `_uscan._tcp` via zeroconf
- `scansnap serve [--port 8090] [--name …]` startet Server + Bonjour
- `app/` – Desktop-App (PySide6, optionales Extra `app`): `document.py` (Seiten, Voreinstellungen, PDF, ohne Qt),
  `main.py` (Fenster, Scan im QThread). Start: `uv run --extra app scansnap-app [--ip …]`
  `primer.py`: Primer-Design (GitHub) – Tokens aus @primer/primitives 11.10.0 als QPalette + Stylesheet,
  Fusion, Hell/Dunkel folgt dem System live. Default-Knopf = Primary (grün), gedämpfter Text per `muted`-Property.
- `button.py` – Scan-Taste am Touchpanel: `ButtonListener` (UDP `host_port`), `Device(on_button=…)` startet ihn
  mit der dauerhaften Sitzung. `scansnap listen [--mode/--dpi/--duplex/--out]` scannt bei jedem Druck;
  die App startet dann einen Scan mit den Einstellungen links und hält die Sitzung (Neuaufbau alle 20 s).
- `images.py` – Zuschnitt, Drehung, `is_blank` (Leerseiten)
- `packaging/` – Mac-App per PyInstaller: `build_mac.sh` → `dist/ScanSnap.app` + `dist/ScanSnap-<ver>.dmg`;
  `ScanSnap.spec` (Qt-Module ausgeschlossen, Info.plist inkl. `NSLocalNetworkUsageDescription`),
  Icon aus `tools/make_icon.py`. Build prüft Signatur + `ScanSnap --selftest` (Paketdaten, JPEG, PDF).
- `tests/` – Mock-Scanner spielt `tests/fixtures/*.json` ab (bereinigt, ohne Bilddaten)
- `tools/make_fixtures.py` – Fixtures aus Rohcaptures erzeugen; Rohcaptures nach `captures/` (gitignored)

## Gesicherte Fakten (aus Captures von ScanSnap Home)
- Kein TLS, keine Krypto. Bilder kommen als fertige JPEGs in Chunks, Seite/Chunk in den
  ersten 2 Datenbytes. Scanner liest immer beide Seiten; Simplex = Rückseite verwerfen.
- Seitenende: Status 2 + Sense EOM (Info = Restlänge). Leerer Einzug: Sense 3/80/03.
- Parameterblöcke E9/D4 für gray/150 und color/600 byte-identisch; andere dpi/Modi abgeleitet.
- Ablauf ScanSnap Home direkt vor jedem Scan: 0x41/0x0100 (get_users) → 0x41/0x0610
  (Profil wählen) → D5 auf dem Scan-Kanal, Abstand ~100–150 ms.
- ScanSnap Home hält 53218 dauerhaft offen: INQUIRY, INQUIRY VPD f0, E7, dann C2 alle 250 ms
  (während des Scans kein C2). C2-Antwort Byte 32–39 = ausgewählter Host.
- UDP-Heartbeat von ScanSnap Home: `ssNR` seq=1 Flag 0x1000 an 52217 alle ~0,5 s,
  Scanner antwortet 12 Byte `ssNR 8001 0000 0000…` (oft mehrfach). Implementiert:
  `discovery.Heartbeat`, läuft in `scan` vor dem Scan-Kanal (`--no-heartbeat` zum Abschalten).
- Scanner schickt beim Pairing Benachrichtigungen an UDP 53220 des Hosts – NICHT ausgewertet.

## Scan-Taste am Panel (2026-10-29, `captures/panel_scan1.pcap`, `panel_scan2.pcap`, `listen1.log`)
- Druck auf „Scan“ → 48-Byte-UDP `ssNR` (u32 1 an Offset 8, Zähler an 16, Scanner-IP/MAC an 20/24) an
  `host_port` des ausgewählten Hosts (55265), 3× im Abstand 0,5 s. Keine Antwort nötig: Panel zeigt
  „Es wird auf eine Antwort vom Gerät gewartet“, bis der Host normal 0x0100 → 0x0610 → D5 … ausführt.
- Parallel C2-Datenbyte 4 Bit 0 für ~0,3 s (SANE `scan_sw`); Datenbyte 3 Bit 7 = Einzug leer.
- Live bestätigt mit `scansnap listen` (2 Drücke, je 1 Blatt). Ohne laufende Sitzung kommt nichts an.
  App-Anbindung nur mit Tests (offscreen) geprüft. Wie lange das Panel auf den Host wartet: unbekannt.

## Kalt-Capture ScanSnap Home (`captures/cold_start.pcap`, 2026-10-29)
- UDP: Probes `VENS`/`ssNR`/`V2ss` von Port 55264 (Port-Feld 55264), 3× im Abstand 0,3 s.
- 53218 (Verbindung A): INQUIRY, VPD f0, E7, C2-Polling.
- 53219: 0x13 → 0x0100 → 0x0210 → 0x11 mit **Flag 0** (Flag 5 nur beim Koppeln). Kein 0x30/0x12.
- Danach Verbindung A schließen, **neue Verbindung B** auf 53218 (nur C2-Polling, kein INQUIRY).
- UDP-Heartbeat erst ab ~5 s nach Start. 0x0210 wiederholt sich (~10 s).
- Scan: 0x0100 → 0x0610 → D5 … wie bisher.
- C2-Statusbyte (Daten-Offset 16): 0x80 vor 0x11, danach 0xe0. Mit Flag 5 dagegen 0xe1.
- **SCSI-Header: Body-Offset 8 = Länge der Daten zum Scanner.** Fehlte bei uns → E9 mit Sense 5/26/00.
- Profil …0010 = color/300. D4 von ScanSnap Home weicht von unserem Template ab
  (Byte 10 0x90, Byte 13 0x00, Byte 40 0x0d), unser color/300-Block wird trotzdem akzeptiert.

## Live-Tests (Mac 192.168.178.37, Scanner 192.168.178.29, ScanSnap Home beendet)
- **Scan funktioniert** (2026-10-29, `scan11.log`): `--identity ssh-identity.json --mode color --dpi 300
  --profile-id 00000000000000000000000000000010` → gültiges JPEG 2608×3688 RGB.
  gray/150 mit demselben Profil läuft bis zum Einzug (kein Papier → sauber erkannt).
- Ausschlaggebend waren: Verbindungsablauf wie im Kalt-Capture (`Control.connect`, Scan-Kanal neu aufbauen)
  und die Out-Länge im SCSI-Header. Ob der Verbindungsablauf allein oder beides nötig war, ist nicht getrennt getestet.
- **Eigene Identität scannt** (2026-10-29, `claim9.log`, `scan21.log`): `claim`, dann `scan --mode color --dpi 300`.
  Voraussetzung: `scansnap-bridge` am Touchpanel als Benutzer gewählt.
- Erkenntnisse dazu:
  - 0x0110 geht nur im Pairing-Zustand (0x11 Flag 5, C2 `e1`), sonst `fffffffe`. Danach Pairing
    abschließen mit 0x30 → 0x12 → 0x11 Flag 0 (`Control.finish_pairing`), sonst bleibt `e1` und
    0x11/0 → `fffffffc`, 0x0610 → `fffffffe`.
  - `0xffffffff` (Quittung, Status an Offset 24) bei: Benutzername > 30 Zeichen, `select` umstellen,
    umsortierter Benutzerliste.
  - `select` (= C2 Byte 32–39) nur am Touchpanel umschaltbar. 0x11 Flag 0 von nicht ausgewähltem Host → `fffffff9`.
  - Nach Benutzerwechsel am Panel liefert 0x0100 nur die Profile des gewählten Benutzers (anfangs leer).
    Vermutung (Nutzer): Profile werden pro Benutzer gespeichert, die von ScanSnap Home sind also noch da.
    Ungeprüft. Sicherung: `captures/users_backup_2026-10-29.json`.
  - Profilvorlage im Paket: `src/scansnap/profile_template.json` (aus „ScanSnap Home“, ohne Namen/IDs/IP).
- Ältere Befunde: D5 ohne 0x0610 → Sense 3/80/33. 0x0110 (eigener Benutzer + Profil) → 0xffffffff.
  Nur Scan-Kanal vor 0x0610 oder nur Heartbeat → `fffffffe` (Ursache war der Ablauf oben).
- Heartbeat-Antworten (12 B `ssNR 8001…`) kommen unzuverlässig. Unicast-Probes: nur `ssNR`/0x0100/seq 0
  und `ssNR`/0x1000/seq 1 werden beantwortet.
- Discovery per Broadcast antwortet nicht immer → `--ip` verwenden.

- **Dauerhafte Sitzung live bestätigt** (2026-10-29, `captures/keepalive1.log`): connect 2,2 s, 12 s gehalten,
  dann zweimal 0x0100 → 0x0610 → D5 auf derselben Verbindung → beide Male sauber NoPaper, Verbindung bleibt.
  Bis „leer“: 2,6 s statt 4,9 s mit Neuverbindung (`keepalive2.log`). Echter Papierscan damit noch ungetestet.

## eSCL-Server (2026-10-29)
- Läuft lokal: Bonjour per `dns-sd -B _uscan._tcp` sichtbar, Status/Capabilities ok, POST ScanJobs
  → Auto-`claim` → Profil gewählt → Einzug (409 bei leerem Einzug). Tests: `tests/test_escl.py` (Fake-Scanner).
- Profile verschwinden am Scanner gelegentlich, auch ohne Benutzerwechsel (Ursache unklar) → `Device.ensure_profile`.
- **Scan aus dem macOS-Scanfenster funktioniert** (2026-10-29): Capabilities → POST ScanJobs 201 →
  NextDocument 200 → 404 → DELETE. Getestet: Farbe simplex/duplex, Duplex-PDF, Schwarzweiß.
- Seiten kommen vom Scanner um 180° gedreht und mit ganzem Scanfeld (Breite 2608 px @300 dpi, Länge mit
  ~50/110 px Hintergrund). `images.process` in `Device.scan`: Zuschnitt (Hintergrund ~238, Papier ~252,
  Schattenkante ~225; Spalten-/Zeilen-75.-Perzentil, Schwelle in der Mitte) + Drehung, ein Re-Encode mit
  gleichen qtables. A4-Test: 210×298 mm, im macOS-Scanfenster bestätigt. `--no-crop` schaltet ab.
  Dunkles Papier → kein Zuschnitt.
- macOS „Schwarzweiß“ = `ColorMode=Grayscale8`. Bei gray-D4 sieht das Bild grau aus, das JPEG hat aber
  3 Kanäle (Scanner liefert immer YCbCr) → ggf. serverseitig in echtes Graustufen-JPEG umwandeln.

## Papierfehler
- Sense 3/80/xx = Medium Error, Tabelle `scsi.MEDIUM_ERRORS` (nach SANE fujitsu.c, aus dem Gedächtnis):
  01 Stau, 02 Abdeckung offen, 03 leer, 04 ungewöhnliches Papier, 07 Doppeleinzug, 33 WLAN-Steuerfehler …
  Live: zerknülltes Blatt (nicht einziehbar) und simulierter Stau → beide 3/80/01.
- `scanner.FeedError(reason)`; App zeigt Hinweis + behält bisherige Seiten; eSCL: Job Aborted/MediaJam.

## Desktop-App (2026-10-29)
- Nutzer will eine echte Desktop-App in Python (keine Weboberfläche, kein Swift), angelehnt an ScanSnap Home,
  reduziert. **Oberfläche auf Englisch** (Code-Kommentare weiter Deutsch).
- Aufbau: links Profile + Optionen (Double-sided, Remove blank pages, Remove background) + Scan;
  Mitte Dokumentliste („New Scan“ + gespeicherte PDFs, bleibt über Neustarts: `app/library.py`,
  Index `library.json` + `thumbs/` in AppData); rechts Seiten (drehen/löschen/sortieren), Name, Ordner, Speichern.
  Gespeicherte Dokumente lassen sich weiter bearbeiten („Save Changes“ schreibt das PDF neu, Umbenennen
  benennt die Datei um). Seiten werden aus den eigenen PDFs zurückgelesen (`read_pdf_pages`).
- Start: ohne gespeicherte oder erreichbare IP (TCP 53219) erscheint zuerst der Dialog „Connect Scanner“
  (IP eingeben oder „Search Network“, Suche/Prüfung im Hintergrund mit Spinner). Verbindungsfehler beim
  Scan öffnen ihn ebenfalls.
- Jeder Scan-Vorgang ist ein eigenes Dokument und wird **sofort nach dem Scan gespeichert** (Nutzerwunsch),
  Name „Scan JJJJ-MM-TT HH.MM“, Ordner „Save to“ (links), optional öffnen. Auch die Seiten vor einem
  Papierstau. Während des Scans ist es ein Entwurf; scheitert das Speichern, bleibt der Entwurf für
  „Save as PDF“ stehen. Scan ohne Seiten verschwindet. Kein Anhängen an bestehende Scans.
- „Remove background“: `images.whiten` – Papierfarbe je Kanal (85.-Perzentil) → Weiß; dunkles Papier
  (< 150) bleibt unverändert. Läuft in `Device.scan(whiten=…)` im selben Re-Encode wie Zuschnitt/Drehung.
- QListWidget im IconMode sortiert beim Ziehen nicht um → `PageList.dropEvent` sortiert selbst.
- Tests: `tests/test_app_document.py`, `test_app_library.py`, `test_app_window.py` (offscreen).
- NOCH NICHT live mit dem Scanner getestet (weder aus Quellen noch als .app).
- .app: arm64, ad hoc signiert (andere Macs: Rechtsklick → Öffnen), ~107 MB, DMG ~46 MB.
  Beim ersten Start fragt macOS nach Zugriff aufs lokale Netzwerk – erlauben.

## Nächste Schritte
1. App live testen, Feedback einarbeiten.
2. Scan-Taste in der App live testen.
3. Graustufen als 1-Kanal-JPEG; Zuschnitt mit Quittung/Visitenkarte/farbigem Papier testen.
- Keine Priorität: Docker/Server, Profile.

## Konventionen
- `uv run --extra app pytest` muss grün bleiben (ohne Extra werden die GUI-Tests übersprungen); neue Erkenntnisse als Fixture/Test absichern.
- Rohcaptures und `ssh-identity.json` nie committen (enthalten Gerätedaten bzw. gescannte Dokumente).
- Antworten an den Nutzer kurz und auf Deutsch.
