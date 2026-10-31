"""The model list in the code and the matrix in docs/models.md stay the same."""

from __future__ import annotations

from pathlib import Path

from scansnap.core import catalog

MATRIX = Path(__file__).parent.parent / "docs" / "models.md"


def matrix_rows() -> list[list[str]]:
    rows = [line.strip().strip("|").split("|") for line in MATRIX.read_text().splitlines() if line.startswith("| ")]
    return [[cell.strip() for cell in row] for row in rows[1:]]  # without the header


def test_matrix_matches_catalog() -> None:
    expected = [[m.full_name, m.connection, catalog.SUPPORT_LABELS[m.support], m.note] for m in catalog.MODELS]
    assert matrix_rows() == expected


def test_support_levels() -> None:
    assert [m.id for m in catalog.MODELS if m.support == "supported"] == ["ix1500"]
    assert [m.id for m in catalog.MODELS if m.support == "beta"] == ["ix500"]
    assert all(m.wifi for m in catalog.MODELS if m.usable)
    assert len({m.id for m in catalog.MODELS}) == len(catalog.MODELS)


def test_match_reported_names() -> None:
    def matched(text: str) -> str | None:
        m = catalog.match(text)
        return m.id if m else None

    assert matched("ScanSnap iX1500 ") == "ix1500"
    assert matched("iX1500-TEST000000") == "ix1500"
    assert matched("ScanSnap iX500  ") == "ix500"
    assert matched("iX500-A0B1C2D3E4") == "ix500"
    assert matched("fi-5110EOX") == "fi-5110eox"
    assert matched("Some other scanner") is None
