import http.client
import io
import threading
import xml.etree.ElementTree as ET

import pytest
from PIL import Image

from scansnap.escl import EsclService, make_server, parse_scan_settings
from scansnap.pdf import jpeg_info, jpegs_to_pdf
from scansnap.scanner import NoPaper, Page

NS = {"scan": "http://schemas.hp.com/imaging/escl/2011/05/03", "pwg": "http://www.pwg.org/schemas/2010/12/sm"}


def jpeg(w=40, h=60, mode="RGB", color="red"):
    buf = io.BytesIO()
    Image.new(mode, (w, h), color).save(buf, "JPEG")
    return buf.getvalue()


def settings_xml(mode="RGB24", dpi=300, duplex=False, fmt="image/jpeg", source="Feeder"):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{NS['scan']}" xmlns:pwg="{NS['pwg']}">
  <pwg:Version>2.63</pwg:Version>
  <pwg:InputSource>{source}</pwg:InputSource>
  <scan:ColorMode>{mode}</scan:ColorMode>
  <scan:XResolution>{dpi}</scan:XResolution><scan:YResolution>{dpi}</scan:YResolution>
  <scan:Duplex>{str(duplex).lower()}</scan:Duplex>
  <scan:DocumentFormatExt>{fmt}</scan:DocumentFormatExt>
</scan:ScanSettings>""".encode()


class FakeScanner:
    def __init__(self, sheets):
        self.sheets, self.calls = sheets, []

    def __call__(self, settings):
        self.calls.append(settings)
        if not self.sheets:
            raise NoPaper("leer")
        for front, back in self.sheets:
            yield Page(front, back if settings.duplex else None, None)


@pytest.fixture
def serve():
    servers = []

    def start(scanner):
        service = EsclService(scanner, serial="SN1", uuid="11111111-2222-3333-4444-555555555555")
        server = make_server(service, "127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return service, server.server_address[1]

    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


def request(port, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request(method, path, body=body, headers={"Content-Type": "text/xml"} if body else {})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r, data


def test_parse_scan_settings_maps_mode_resolution_duplex():
    req = parse_scan_settings(settings_xml("Grayscale8", 200, True, "application/pdf"))
    assert (req.settings.mode, req.settings.dpi, req.settings.duplex, req.fmt) == ("gray", 150, True, "application/pdf")
    assert parse_scan_settings(settings_xml(dpi=1200)).settings.dpi == 600


def test_capabilities_and_status(serve):
    _, port = serve(FakeScanner([]))
    r, data = request(port, "GET", "/eSCL/ScannerCapabilities")
    assert r.status == 200
    root = ET.fromstring(data)
    assert root.find("scan:UUID", NS).text == "11111111-2222-3333-4444-555555555555"
    caps = root.find("scan:Adf/scan:AdfDuplexInputCaps", NS)
    assert {e.text for e in caps.iter(f"{{{NS['scan']}}}XResolution")} == {"150", "300", "600"}
    r, data = request(port, "GET", "/eSCL/ScannerStatus")
    assert ET.fromstring(data).find("pwg:State", NS).text == "Idle"


def test_duplex_jpeg_job_delivers_front_back_then_404(serve):
    a, b, c = jpeg(color="red"), jpeg(color="blue"), jpeg(color="green")
    scanner = FakeScanner([(a, b), (c, jpeg())])
    _, port = serve(scanner)
    r, _ = request(port, "POST", "/eSCL/ScanJobs", settings_xml(duplex=True))
    assert r.status == 201
    path = r.getheader("Location").split(f":{port}", 1)[1] + "/NextDocument"
    docs = []
    while True:
        r, data = request(port, "GET", path)
        if r.status == 404:
            break
        assert r.getheader("Content-Type") == "image/jpeg"
        docs.append(data)
    assert docs[:3] == [a, b, c] and len(docs) == 4
    assert scanner.calls[0].duplex and scanner.calls[0].dpi == 300
    r, data = request(port, "GET", "/eSCL/ScannerStatus")
    assert ET.fromstring(data).find("scan:Jobs/scan:JobInfo/pwg:JobState", NS).text == "Completed"


def test_pdf_job_returns_single_document(serve):
    _, port = serve(FakeScanner([(jpeg(), jpeg()), (jpeg(mode="L", color=128), jpeg())]))
    r, _ = request(port, "POST", "/eSCL/ScanJobs", settings_xml(fmt="application/pdf"))
    path = r.getheader("Location").split(f":{port}", 1)[1] + "/NextDocument"
    r, data = request(port, "GET", path)
    assert r.status == 200 and r.getheader("Content-Type") == "application/pdf"
    assert data.startswith(b"%PDF-1.4") and data.count(b"/Type /Page ") == 2 and b"/DeviceGray" in data
    assert request(port, "GET", path)[0].status == 404


def test_empty_feeder_returns_409_and_status_reports_empty(serve):
    _, port = serve(FakeScanner([]))
    r, _ = request(port, "POST", "/eSCL/ScanJobs", settings_xml())
    assert r.status == 409
    r, data = request(port, "GET", "/eSCL/ScannerStatus")
    assert ET.fromstring(data).find("scan:AdfState", NS).text == "ScannerAdfEmpty"


def test_platen_is_rejected(serve):
    _, port = serve(FakeScanner([(jpeg(), jpeg())]))
    assert request(port, "POST", "/eSCL/ScanJobs", settings_xml(source="Platen"))[0].status == 409


def test_pdf_page_size_follows_dpi():
    pdf = jpegs_to_pdf([jpeg(300, 600)], dpi=300)
    assert b"/MediaBox [0 0 72.00 144.00]" in pdf
    assert jpeg_info(jpeg(30, 20, "L")) == (30, 20, 1)


def test_scanner_error_before_first_page_returns_500(serve):
    def broken(settings):
        raise RuntimeError("Scanner hat keine Profile.")
        yield

    _, port = serve(broken)
    assert request(port, "POST", "/eSCL/ScanJobs", settings_xml())[0].status == 500


def test_rotate_180_flips_pixels_and_keeps_size():
    from scansnap.images import rotate_180

    im = Image.new("RGB", (64, 32), "white")
    im.paste((255, 0, 0), (0, 0, 16, 16))          # rotes Quadrat oben links
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    out = Image.open(io.BytesIO(rotate_180(buf.getvalue())))
    assert out.size == (64, 32)
    r, g, b = out.getpixel((56, 24))                 # jetzt unten rechts
    assert r > 200 and g < 60 and b < 60
    assert out.getpixel((4, 4))[1] > 200             # oben links weiß
