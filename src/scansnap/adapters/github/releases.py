"""Latest release via the GitHub REST API (without a token: 60 requests per hour and IP are enough at startup).

Checksums: since 2025-06 GitHub provides ``digest: "sha256:…"`` for every asset; if it is missing, the
``SHA256SUMS`` file attached by the release workflow is read.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import IO, Any, cast

from ...core.update import Asset, Release, parse_sha256sums, parse_version

REPO = "elte0/scansnack"
API = "https://api.github.com"
SUMS = "SHA256SUMS"

Opener = Callable[[urllib.request.Request, float], IO[bytes]]


def _open(request: urllib.request.Request, timeout: float) -> IO[bytes]:
    return cast(IO[bytes], urllib.request.urlopen(request, timeout=timeout))


def _digest(raw: dict[str, Any]) -> str | None:
    digest = raw.get("digest")
    if isinstance(digest, str) and digest.startswith("sha256:"):
        return digest.removeprefix("sha256:").lower()
    return None


def parse_release(data: object, sums: dict[str, str] | None = None) -> Release | None:
    """JSON of ``GET /repos/…/releases/latest`` → Release; None for a draft, pre-release or foreign tag."""
    if not isinstance(data, dict):
        raise ValueError("unexpected release data")
    raw = cast(dict[str, Any], data)
    version = parse_version(str(raw.get("tag_name", "")))
    if version is None or raw.get("draft") or raw.get("prerelease"):
        return None
    assets: list[Asset] = []
    for item in cast(list[dict[str, Any]], raw.get("assets") or []):
        name = str(item["name"])
        sha = _digest(item) or (sums or {}).get(name)
        assets.append(Asset(name, str(item["browser_download_url"]), int(item.get("size", 0)), sha))
    return Release(
        version, str(raw["tag_name"]), str(raw.get("html_url", "")), str(raw.get("body") or ""), tuple(assets)
    )


class GitHubReleases:
    """``ReleaseSource`` for a public GitHub repository."""

    def __init__(
        self, repo: str = REPO, *, timeout: float = 10.0, opener: Opener = _open, user_agent: str = "ScanSnack"
    ):
        self.repo = repo
        self.timeout = timeout
        self.opener = opener
        self.user_agent = user_agent

    def _get(self, url: str, accept: str) -> bytes:
        request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": self.user_agent})
        with self.opener(request, self.timeout) as response:
            return response.read()

    def latest(self) -> Release | None:
        try:
            body = self._get(f"{API}/repos/{self.repo}/releases/latest", "application/vnd.github+json")
        except urllib.error.HTTPError as e:
            if e.code == 404:  # no release published yet
                return None
            raise
        data = json.loads(body)
        release = parse_release(data)
        if release is None or all(a.sha256 for a in release.assets):
            return release
        sums_asset = next((a for a in release.assets if a.name == SUMS), None)
        if sums_asset is None:
            return release
        sums = parse_sha256sums(self._get(sums_asset.url, "application/octet-stream").decode("utf-8", "replace"))
        return parse_release(data, sums)
