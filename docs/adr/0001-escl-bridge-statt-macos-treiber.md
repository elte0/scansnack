---
status: akzeptiert
date: 2026-10-29
---

# eSCL-Bridge statt nativem macOS-Treiber

## Kontext und Problemstellung

Der ScanSnap iX1500 soll per WLAN im macOS-Scanfenster (Digitalbilder, Vorschau, Druckerdialog) nutzbar
sein, ohne ScanSnap Home. Der Scanner spricht nur ein proprietäres Protokoll (docs/protocol.md). Wie
bringen wir ihn ins Scanfenster?

## Entscheidungstreiber

* Kein Eingriff ins System: keine Treiberinstallation, keine Kernel-/System-Erweiterung, keine Signierung
  als Treiber.
* Später als Docker-Container mit Host-Networking auf dem Server laufen (ein Dienst für alle Rechner im Netz).
* Protokoll-Client in Python, testbar gegen Mitschnitte.

## Erwogene Optionen

* eSCL/AirScan-Server (HTTP + Bonjour `_uscan._tcp`), der Aufträge an den Scanner weiterreicht
* nativer macOS-Scannertreiber (Image-Capture-Treiber)

## Entscheidung

Gewählt: **eSCL/AirScan-Bridge**. macOS findet eSCL-Scanner per Bonjour von selbst und braucht dafür
keinen Treiber; dieselbe Bridge kann auf einem Server laufen und dient dann allen Macs (und anderen
eSCL-Clients) im Netz.

### Konsequenzen

* Gut: läuft ohne Installation auf den Clients; Bridge und Protokoll-Client bleiben Python.
* Gut: Funktionsumfang über eSCL-Standard begrenzt und damit klar (nur Einzug, JPEG/PDF).
* Schlecht: Funktionen, die eSCL nicht abbildet (Touchpanel-Taste, Profile), brauchen eigene Oberflächen
  (Desktop-App, siehe ADR 0003).
* Schlecht: zusätzlicher Dienst, der laufen muss, solange gescannt werden soll.

### Bestätigung

Scan aus dem macOS-Scanfenster live bestätigt (2026-10-29): Capabilities → POST ScanJobs 201 →
NextDocument 200 → 404 → DELETE; Farbe simplex/duplex, Duplex-PDF, Schwarzweiß.
Tests: `tests/test_escl.py`.
