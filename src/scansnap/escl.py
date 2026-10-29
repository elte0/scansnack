"""eSCL/AirScan-Server (Mopria/Apple), der Aufträge an den ScanSnap weiterreicht.

Endpunkte:
    GET    /eSCL/ScannerCapabilities
    GET    /eSCL/ScannerStatus
    POST   /eSCL/ScanJobs                     → 201 + Location, 409 bei leerem Einzug
    GET    /eSCL/ScanJobs/<id>/NextDocument   → nächste Seite, 404 wenn keine mehr kommt
    DELETE /eSCL/ScanJobs/<id>

Nur Einzug (ADF). Der Scanner liefert fertige JPEGs; für PDF werden sie unverändert eingebettet.
Längen in eSCL sind in 1/300 Zoll.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
import traceback
import uuid as uuidlib
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape

from .pdf import jpeg_info, jpegs_to_pdf
from .scanner import HEIGHT_1200, WIDTH_1200, FeedError, NoPaper, Page, Settings

ESCL_VERSION = "2.63"
RESOLUTIONS = (150, 300, 600)
FORMATS = ("image/jpeg", "application/pdf")
MAX_WIDTH = WIDTH_1200 // 4               # 1/1200 → 1/300 Zoll
MAX_HEIGHT = HEIGHT_1200 // 4
FIRST_PAGE_TIMEOUT = 60.0

NS = ('xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03" '
      'xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm"')

ScanFn = Callable[[Settings], Iterator[Page]]


@dataclass(frozen=True)
class ScanRequest:
    settings: Settings
    fmt: str
    color_mode: str = "RGB24"          # wie vom Client angefragt


class BadRequest(Exception):
    pass


def parse_scan_settings(body: bytes) -> ScanRequest:
    """ScanSettings-XML → Settings. Namensräume werden ignoriert (Clients sind da uneinheitlich)."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise BadRequest(f"ScanSettings nicht lesbar: {e}") from e
    values: dict[str, str] = {}
    for el in root.iter():
        values.setdefault(el.tag.rsplit("}", 1)[-1], (el.text or "").strip())
    source = values.get("InputSource", "Feeder")
    if source not in ("Feeder", "Adf"):
        raise BadRequest(f"InputSource {source} nicht unterstützt (nur Feeder)")
    color_mode = values.get("ColorMode", "RGB24")
    mode = "color" if color_mode == "RGB24" else "gray"
    try:
        wanted = int(values.get("XResolution") or 300)
    except ValueError as e:
        raise BadRequest("XResolution ungültig") from e
    dpi = min(RESOLUTIONS, key=lambda r: (abs(r - wanted), r))
    duplex = values.get("Duplex", "false").lower() in ("true", "1")
    fmt = values.get("DocumentFormatExt") or values.get("DocumentFormat") or "image/jpeg"
    if fmt not in FORMATS:
        fmt = "image/jpeg"
    return ScanRequest(Settings(mode, dpi, duplex), fmt, color_mode)


def _input_caps(tag: str) -> str:
    modes = "".join(f"<scan:ColorMode>{m}</scan:ColorMode>" for m in ("RGB24", "Grayscale8"))
    formats = "".join(f"<pwg:DocumentFormat>{f}</pwg:DocumentFormat><scan:DocumentFormatExt>{f}</scan:DocumentFormatExt>"
                      for f in FORMATS)
    res = "".join(f"<scan:DiscreteResolution><scan:XResolution>{r}</scan:XResolution>"
                  f"<scan:YResolution>{r}</scan:YResolution></scan:DiscreteResolution>" for r in RESOLUTIONS)
    return f"""<scan:{tag}>
<scan:MinWidth>300</scan:MinWidth><scan:MaxWidth>{MAX_WIDTH}</scan:MaxWidth>
<scan:MinHeight>300</scan:MinHeight><scan:MaxHeight>{MAX_HEIGHT}</scan:MaxHeight>
<scan:MaxScanRegions>1</scan:MaxScanRegions>
<scan:SettingProfiles><scan:SettingProfile>
<scan:ColorModes>{modes}</scan:ColorModes>
<scan:DocumentFormats>{formats}</scan:DocumentFormats>
<scan:SupportedResolutions><scan:DiscreteResolutions>{res}</scan:DiscreteResolutions></scan:SupportedResolutions>
<scan:ColorSpaces><scan:ColorSpace>sRGB</scan:ColorSpace></scan:ColorSpaces>
</scan:SettingProfile></scan:SettingProfiles>
<scan:SupportedIntents><scan:Intent>Document</scan:Intent><scan:Intent>TextAndGraphic</scan:Intent><scan:Intent>Photo</scan:Intent></scan:SupportedIntents>
<scan:MaxOpticalXResolution>600</scan:MaxOpticalXResolution><scan:MaxOpticalYResolution>600</scan:MaxOpticalYResolution>
<scan:RiskyLeftMargin>0</scan:RiskyLeftMargin><scan:RiskyRightMargin>0</scan:RiskyRightMargin>
<scan:RiskyTopMargin>0</scan:RiskyTopMargin><scan:RiskyBottomMargin>0</scan:RiskyBottomMargin>
</scan:{tag}>"""


def capabilities_xml(make_and_model: str, serial: str, uuid: str, admin_url: str = "") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScannerCapabilities {NS}>
<pwg:Version>{ESCL_VERSION}</pwg:Version>
<pwg:MakeAndModel>{escape(make_and_model)}</pwg:MakeAndModel>
<pwg:SerialNumber>{escape(serial)}</pwg:SerialNumber>
<scan:UUID>{uuid}</scan:UUID>
<scan:AdminURI>{escape(admin_url)}</scan:AdminURI>
<scan:Adf>
{_input_caps("AdfSimplexInputCaps")}
{_input_caps("AdfDuplexInputCaps")}
<scan:FeederCapacity>50</scan:FeederCapacity>
<scan:AdfOptions><scan:AdfOption>Duplex</scan:AdfOption></scan:AdfOptions>
</scan:Adf>
</scan:ScannerCapabilities>
"""


@dataclass
class Job:
    request: ScanRequest
    id: str = field(default_factory=lambda: uuidlib.uuid4().hex)
    created: float = field(default_factory=time.monotonic)
    documents: queue.Queue = field(default_factory=queue.Queue)   # bytes | None (Ende)
    started: threading.Event = field(default_factory=threading.Event)
    state: str = "Processing"          # Processing | Completed | Aborted | Canceled
    reason: str = "JobScanning"
    images: int = 0
    delivered: int = 0
    no_paper: bool = False

    @property
    def uri(self) -> str:
        return f"/eSCL/ScanJobs/{self.id}"

    def info_xml(self) -> str:
        return (f"<scan:JobInfo><pwg:JobUri>{self.uri}</pwg:JobUri><pwg:JobUuid>{self.id}</pwg:JobUuid>"
                f"<scan:Age>{int(time.monotonic() - self.created)}</scan:Age>"
                f"<pwg:ImagesCompleted>{self.images}</pwg:ImagesCompleted>"
                f"<pwg:ImagesToTransfer>{max(self.images - self.delivered, 0)}</pwg:ImagesToTransfer>"
                f"<pwg:JobState>{self.state}</pwg:JobState>"
                f"<pwg:JobStateReasons><pwg:JobStateReason>{self.reason}</pwg:JobStateReason></pwg:JobStateReasons>"
                f"</scan:JobInfo>")


class EsclService:
    """Auftragsverwaltung ohne HTTP; ein Scan zur Zeit."""

    def __init__(self, scan: ScanFn, *, make_and_model: str = "ScanSnap iX1500", serial: str = "",
                 uuid: str | None = None, admin_url: str = ""):
        self.scan = scan
        self.make_and_model, self.serial, self.admin_url = make_and_model, serial, admin_url
        self.uuid = uuid or str(uuidlib.uuid4())
        self.jobs: dict[str, Job] = {}
        self.adf_empty = False
        self._busy = threading.Lock()

    @property
    def busy(self) -> bool:
        return self._busy.locked()

    def capabilities(self) -> str:
        return capabilities_xml(self.make_and_model, self.serial, self.uuid, self.admin_url)

    def status(self) -> str:
        adf = "ScannerAdfEmpty" if self.adf_empty else "ScannerAdfLoaded"
        jobs = "".join(j.info_xml() for j in sorted(self.jobs.values(), key=lambda j: -j.created))
        return (f'<?xml version="1.0" encoding="UTF-8"?>\n<scan:ScannerStatus {NS}>'
                f"<pwg:Version>{ESCL_VERSION}</pwg:Version>"
                f"<pwg:State>{'Processing' if self.busy else 'Idle'}</pwg:State>"
                f"<scan:AdfState>{adf}</scan:AdfState><scan:Jobs>{jobs}</scan:Jobs></scan:ScannerStatus>\n")

    def start(self, request: ScanRequest) -> Job | None:
        """Startet den Scan und wartet auf die erste Seite. None = Einzug leer, BlockingIOError = belegt."""
        if not self._busy.acquire(blocking=False):
            raise BlockingIOError("Scanner belegt")
        job = Job(request)
        self._prune()
        self.jobs[job.id] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        job.started.wait(FIRST_PAGE_TIMEOUT)
        if job.no_paper:
            return None
        if job.state == "Aborted" and job.images == 0:
            raise RuntimeError("Scan fehlgeschlagen")
        return job

    def _run(self, job: Job) -> None:
        req = job.request
        collected: list[bytes] = []

        def emit(doc: bytes) -> None:
            job.images += 1
            w, h, comps = jpeg_info(doc)
            print(f"[escl] Seite {job.images}: {w}×{h}, {comps} Kanal/Kanäle, {len(doc)} B", file=sys.stderr)
            if req.fmt == "application/pdf":
                collected.append(doc)
            else:
                job.documents.put(doc)
            job.started.set()

        try:
            for page in self.scan(req.settings):
                if job.state == "Canceled":
                    break
                emit(page.front)
                if page.back is not None:
                    emit(page.back)
            if collected:
                job.documents.put(jpegs_to_pdf(collected, req.settings.dpi))
            self.adf_empty = False
            if job.state != "Canceled":
                job.state, job.reason = "Completed", "JobCompletedSuccessfully"
        except NoPaper:
            job.no_paper, self.adf_empty = True, True
            job.state, job.reason = "Aborted", "AdfEmpty"
        except FeedError as e:
            print(f"[escl] Papierfehler: {e.reason}", file=sys.stderr)
            job.state, job.reason = "Aborted", "MediaJam" if e.reason == "paper jam" else "ErrorOccurred"
        except Exception:
            traceback.print_exc(file=sys.stderr)
            job.state, job.reason = "Aborted", "ErrorOccurred"
        finally:
            job.documents.put(None)
            job.started.set()
            self._busy.release()

    def next_document(self, job_id: str, timeout: float = 120.0) -> bytes | None:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        try:
            doc = job.documents.get(timeout=timeout)
        except queue.Empty:
            return None
        if doc is None:
            job.documents.put(None)       # weitere Abfragen ebenfalls 404
            return None
        job.delivered += 1
        return doc

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None:
            return False
        if job.state == "Processing":
            job.state, job.reason = "Canceled", "JobCanceledByUser"
        return True

    def _prune(self, keep: int = 10) -> None:
        for j in sorted(self.jobs.values(), key=lambda j: j.created)[:-keep or None]:
            if j.state != "Processing":
                del self.jobs[j.id]


def make_handler(service: EsclService) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "scansnap-bridge"

        def log_message(self, fmt: str, *args) -> None:
            print(f"[escl] {self.address_string()} {fmt % args}", file=sys.stderr)

        def _send(self, status: int, body: bytes = b"", ctype: str = "text/xml",
                  headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            if body:
                self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if body and self.command != "HEAD":
                self.wfile.write(body)

        def _path(self) -> list[str]:
            return [p for p in self.path.split("?", 1)[0].split("/") if p]

        def do_GET(self) -> None:
            parts = self._path()
            if parts == ["eSCL", "ScannerCapabilities"]:
                return self._send(200, service.capabilities().encode())
            if parts == ["eSCL", "ScannerStatus"]:
                return self._send(200, service.status().encode())
            if len(parts) == 4 and parts[:2] == ["eSCL", "ScanJobs"] and parts[3] == "NextDocument":
                job = service.jobs.get(parts[2])
                doc = service.next_document(parts[2])
                if doc is None:
                    return self._send(HTTPStatus.NOT_FOUND)
                return self._send(200, doc, job.request.fmt if job else "application/octet-stream")
            self._send(HTTPStatus.NOT_FOUND)

        do_HEAD = do_GET

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self._path() != ["eSCL", "ScanJobs"]:
                return self._send(HTTPStatus.NOT_FOUND)
            try:
                request = parse_scan_settings(body)
                s = request.settings
                print(f"[escl] Auftrag: ColorMode={request.color_mode} → {s.mode}/{s.dpi} dpi, "
                      f"{'duplex' if s.duplex else 'simplex'}, {request.fmt}",
                      file=sys.stderr)
                job = service.start(request)
            except BadRequest as e:
                print(f"[escl] {e}", file=sys.stderr)
                return self._send(HTTPStatus.CONFLICT)
            except BlockingIOError:
                return self._send(HTTPStatus.SERVICE_UNAVAILABLE)
            except RuntimeError:
                return self._send(HTTPStatus.INTERNAL_SERVER_ERROR)
            if job is None:
                return self._send(HTTPStatus.CONFLICT)
            host = self.headers.get("Host") or f"{self.server.server_address[0]}:{self.server.server_address[1]}"
            self._send(HTTPStatus.CREATED, headers={"Location": f"http://{host}{job.uri}"})

        def do_DELETE(self) -> None:
            parts = self._path()
            if len(parts) == 3 and parts[:2] == ["eSCL", "ScanJobs"] and service.cancel(parts[2]):
                return self._send(200)
            self._send(HTTPStatus.NOT_FOUND)

    return Handler


def make_server(service: EsclService, host: str = "", port: int = 8090) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(service))
    server.daemon_threads = True
    return server
