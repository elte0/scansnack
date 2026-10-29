"""ScanSnap iX1500 per WLAN: eigener Client, eSCL/AirScan-Bridge, Desktop-App.

Aufbau (hexagonal, siehe docs/adr/0002-hexagonale-architektur.md):
``core`` (Domäne) ← ``ports`` (Schnittstellen) ← ``adapters`` (ix1500, escl, library, qt) ← ``app`` (Verdrahtung).
"""
