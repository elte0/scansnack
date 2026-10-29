# scansnap-bridge

WLAN-Client für den ScanSnap iX1500, nachgebaut aus Mitschnitten von ScanSnap Home.
Grundlage für eine eSCL/AirScan-Bridge (folgt).

## Nutzung

```
uv sync
uv run scansnap discover
uv run scansnap pair                         # Handshake wie ScanSnap Home
uv run scansnap scan --mode gray --dpi 150 --duplex --out scans/
uv run scansnap scan --no-pair ...           # testen, ob es ohne Handshake geht
```

Die Host-Identität (Host-ID, User-ID, MAC) liegt in `~/.config/scansnap-bridge/identity.json`.

## Stand

| Teil | Status |
|---|---|
| Framing, Discovery, Steuerkanal, SCSI-Tunnel | gegen Captures getestet |
| Parameterblöcke gray/150 und color/600 | byte-identisch zu den Captures |
| andere dpi/Modi | Felder abgeleitet, nicht belegt (Warnung im CLI) |
| Zuschnitt auf tatsächliche Seitenlänge | offen (Bildinfo wird gelesen) |

## Tests

`uv run pytest` – ein Mock-Scanner spielt die Fixtures ab und erzeugt synthetische Bilder.

Fixtures neu erzeugen (Rohcaptures liegen nicht im Repo):

```
uv run python tools/make_fixtures.py captures/ tests/fixtures/ --scanner-ip <IP>
```
