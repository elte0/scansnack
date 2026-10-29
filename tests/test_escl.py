import http.client
import io
import threading
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from http.server import ThreadingHTTPServer

import pytest
from PIL import Image

from helpers import jpeg as make_jpeg
from helpers import pixel
from scansnap.adapters.escl.server import EsclService, make_server, parse_scan_settings
from scansnap.core.errors import NoPaper
from scansnap.core.images import rotate_180
from scansnap.core.model import ScanSettings, Sheet
from scansnap.core.pdf import jpeg_info, jpegs_to_pdf

NS = {"scan": "http://schemas.hp.com/imaging/escl/2011/05/03", "pwg": "http://www.pwg.org/schemas/2010/12/sm"}


def jpeg(w: int = 40, h: int = 60, mode: str = "RGB", color: str | int = "red") -> bytes:
    return make_jpeg(w, h, color, mode)


def settings_xml(
    mode: str = "RGB24", dpi: int = 300, duplex: bool = False, fmt: str = "image/jpeg", source: str = "Feeder"
) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{NS["scan"]}" xmlns:pwg="{NS["pwg"]}">
  <pwg:Version>2.63</pwg:Version>
  <pwg:InputSource>{source}</pwg:InputSource>
  <scan:ColorMode>{mode}</scan:ColorMode>
  <scan:XResolution>{dpi}</scan:XResolution><scan:YResolution>{dpi}</scan:YResolution>
  <scan:Duplex>{str(duplex).lower()}</scan:Duplex>
  <scan:DocumentFormatExt>{fmt}</scan:DocumentFormatExt>
</scan:ScanSettings>""".encode()


class FakeScanner:
    """ScannerPort mit festen Blättern; ohne Blätter: Einzug leer."""

    connected = True

    def __init__(self, sheets: list[tuple[bytes, bytes]]):
        self.sheets = sheets
        self.calls: list[ScanSettings] = []

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def scan(self, settings: ScanSettings, *, whiten: bool = False) -> Iterator[Sheet]:
        self.calls.append(settings)
        if not self.sheets:
            raise NoPaper("leer")
        for front, back in self.sheets:
            yield Sheet(front, back if settings.duplex else None, None)


Serve = Callable[[FakeScanner], tuple[EsclService, int]]


@pytest.fixture
def serve() -> Iterator[Serve]:
    servers: list[ThreadingHTTPServer] = []

    def start(scanner: FakeScanner) -> tuple[EsclService, int]:
        service = EsclService(scanner, serial="SN1", uuid="11111111-2222-3333-4444-555555555555")
        server = make_server(service, "127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return service, server.server_address[1]

    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


def request(port: int, method: str, path: str, body: bytes | None = None) -> tuple[http.client.HTTPResponse, bytes]:
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request(method, path, body=body, headers={"Content-Type": "text/xml"} if body else {})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r, data


def text(root: ET.Element, path: str) -> str | None:
    el = root.find(path, NS)
    assert el is not None, path
    return el.text


def next_document_path(r: http.client.HTTPResponse, port: int) -> str:
    location = r.getheader("Location")
    assert location is not None
    return location.split(f":{port}", 1)[1] + "/NextDocument"


def test_parse_scan_settings_maps_mode_resolution_duplex() -> None:
    req = parse_scan_settings(settings_xml("Grayscale8", 200, True, "application/pdf"))
    assert (req.settings.mode, req.settings.dpi, req.settings.duplex, req.fmt) == ("gray", 150, True, "application/pdf")
    assert parse_scan_settings(settings_xml(dpi=1200)).settings.dpi == 600


def test_capabilities_and_status(serve: Serve) -> None:
    _, port = serve(FakeScanner([]))
    r, data = request(port, "GET", "/eSCL/ScannerCapabilities")
    assert r.status == 200
    root = ET.fromstring(data)
    assert text(root, "scan:UUID") == "11111111-2222-3333-4444-555555555555"
    caps = root.find("scan:Adf/scan:AdfDuplexInputCaps", NS)
    assert caps is not None
    assert {e.text for e in caps.iter(f"{{{NS['scan']}}}XResolution")} == {"150", "300", "600"}
    r, data = request(port, "GET", "/eSCL/ScannerStatus")
    assert text(ET.fromstring(data), "pwg:State") == "Idle"


def test_duplex_jpeg_job_delivers_front_back_then_404(serve: Serve) -> None:
    a, b, c = jpeg(color="red"), jpeg(color="blue"), jpeg(color="green")
    scanner = FakeScanner([(a, b), (c, jpeg())])
    _, port = serve(scanner)
    r, _ = request(port, "POST", "/eSCL/ScanJobs", settings_xml(duplex=True))
    assert r.status == 201
    path = next_document_path(r, port)
    docs: list[bytes] = []
    while True:
        r, data = request(port, "GET", path)
        if r.status == 404:
            break
        assert r.getheader("Content-Type") == "image/jpeg"
        docs.append(data)
    assert docs[:3] == [a, b, c] and len(docs) == 4
    assert scanner.calls[0].duplex and scanner.calls[0].dpi == 300
    r, data = request(port, "GET", "/eSCL/ScannerStatus")
    assert text(ET.fromstring(data), "scan:Jobs/scan:JobInfo/pwg:JobState") == "Completed"


def test_pdf_job_returns_single_document(serve: Serve) -> None:
    _, port = serve(FakeScanner([(jpeg(), jpeg()), (jpeg(mode="L", color=128), jpeg())]))
    r, _ = request(port, "POST", "/eSCL/ScanJobs", settings_xml(fmt="application/pdf"))
    path = next_document_path(r, port)
    r, data = request(port, "GET", path)
    assert r.status == 200 and r.getheader("Content-Type") == "application/pdf"
    assert data.startswith(b"%PDF-1.4") and data.count(b"/Type /Page ") == 2 and b"/DeviceGray" in data
    assert request(port, "GET", path)[0].status == 404


def test_empty_feeder_returns_409_and_status_reports_empty(serve: Serve) -> None:
    _, port = serve(FakeScanner([]))
    r, _ = request(port, "POST", "/eSCL/ScanJobs", settings_xml())
    assert r.status == 409
    r, data = request(port, "GET", "/eSCL/ScannerStatus")
    assert text(ET.fromstring(data), "scan:AdfState") == "ScannerAdfEmpty"


def test_platen_is_rejected(serve: Serve) -> None:
    _, port = serve(FakeScanner([(jpeg(), jpeg())]))
    assert request(port, "POST", "/eSCL/ScanJobs", settings_xml(source="Platen"))[0].status == 409


def test_pdf_page_size_follows_dpi() -> None:
    pdf = jpegs_to_pdf([jpeg(300, 600)], dpi=300)
    assert b"/MediaBox [0 0 72.00 144.00]" in pdf
    assert jpeg_info(jpeg(30, 20, "L", 255)) == (30, 20, 1)


def test_scanner_error_before_first_page_returns_500(serve: Serve) -> None:
    class Broken(FakeScanner):
        def scan(self, settings: ScanSettings, *, whiten: bool = False) -> Iterator[Sheet]:
            raise RuntimeError("Scanner hat keine Profile.")

    _, port = serve(Broken([]))
    assert request(port, "POST", "/eSCL/ScanJobs", settings_xml())[0].status == 500


def test_rotate_180_flips_pixels_and_keeps_size() -> None:
    im = Image.new("RGB", (64, 32), "white")
    im.paste((255, 0, 0), (0, 0, 16, 16))  # rotes Quadrat oben links
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    out = Image.open(io.BytesIO(rotate_180(buf.getvalue())))
    assert out.size == (64, 32)
    r, g, b = pixel(out, (56, 24))  # jetzt unten rechts
    assert r > 200 and g < 60 and b < 60
    assert pixel(out, (4, 4))[1] > 200  # oben links weiß
