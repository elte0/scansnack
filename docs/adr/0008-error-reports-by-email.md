---
status: accepted
date: 2026-11-03
---

# Error reports sent by the user's own mail program

## Context and problem statement

Users report scan failures with a screenshot or a single line of text ("registration rejected, status -3",
"conn_user", "command 0xd4 failed: sense key=0x5 …"). To understand them we also need the app version,
the operating system, the scanner model and firmware, the step of the protocol that failed, the traceback
and the log lines before it. The app should put all of this into one report that the user can send with
one click to a fixed address (scansnack@posteo.de), without personal data.

## Decision drivers

* No credentials in the app: it is open source, so any SMTP password or API key in it is public.
* No server of our own to run, and no telemetry (the app page promises none).
* The user sees exactly what is sent and decides whether to send it.
* Works on macOS, Windows and Linux; fits the hexagonal layout (ADR 0002).

## Considered options

* SMTP from the app with an account of the project
* An HTTP endpoint (own server, or a form/issue service) that receives reports
* A pre-filled GitHub issue (`issues/new?body=…`)
* A `mailto:` link that opens the user's mail program with the report as the message

## Decision outcome

Chosen: **`mailto:` link**, with *Copy* and *Save* as fallbacks.

* SMTP and an HTTP endpoint need a secret in the app or a server that accepts anything from anyone;
  both invite spam and abuse, and an endpoint is a service to operate.
* A GitHub issue needs a GitHub account and makes the report public.
* `mailto:` needs nothing: the user's mail program sends the message from the user's own address (so we
  can answer), and nothing leaves the computer until the user presses *Send* there.

The report is built in `core/report.py` (no Qt): app and system facts, the scanner adapter's
`diagnostics()` (`ports.scanner.Diagnosable`: model, firmware if known, connection state, last protocol
step; no network I/O), the error and traceback, and the last lines of stderr (`LogBuffer`, filled by a
`Tee` installed in `app/desktop.py`). `scrub()` then removes values the adapters and the window name as
private (scanner IP, MAC, serial, host and user IDs, password, scan folder, document names) plus anything
that looks like an IP or MAC address, email address, UUID, long hex string, user folder, document file
name or `password=…`. The dialog (`adapters/qt/report.py`) shows the editable text before sending.

### Consequences

* Good: no secret, no server, no telemetry; the user reviews and edits the report.
* Bad: the user needs a mail program set up. Without one (or when it does not open), the report goes to
  the clipboard and can be saved as a file to attach to a webmail message.
* Bad: URL length. Windows cuts `mailto:` URLs at about 2,000 characters, so there the body is shortened
  to fit and the full report is put on the clipboard, with a note in the message to paste it. macOS and
  Linux take up to 60,000 characters.
* Raw protocol frames are not part of the report: they can carry the MAC, host ID, serial and the encoded
  iX500 password. Hex strings of 8 bytes or more are replaced. The adapters' "last step" stands in for them.
