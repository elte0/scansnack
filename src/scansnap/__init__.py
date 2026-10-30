"""ScanSnap iX1500 over Wi-Fi: our own client, eSCL/AirScan bridge, desktop app.

Structure (hexagonal, see docs/adr/0002-hexagonal-architecture.md):
``core`` (domain) ← ``ports`` (interfaces) ← ``adapters`` (ix1500, escl, library, qt) ← ``app`` (wiring).
"""
