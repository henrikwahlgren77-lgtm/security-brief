"""Approved-source list: which hosts the collector may fetch from."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "approved_sources.toml"


@dataclass(frozen=True, slots=True)
class ApprovedSource:
    name: str
    country: str
    region: str
    tier: int
    domains: tuple[str, ...]


class Allowlist:
    def __init__(self, sources: list[ApprovedSource]):
        self.sources = list(sources)

    @classmethod
    def load(cls, path: Path | str | None = None) -> "Allowlist":
        data = tomllib.loads(Path(path or DEFAULT_PATH).read_text(encoding="utf-8"))
        sources = []
        for entry in data.get("source", []):
            domains = tuple(d.strip().lower().lstrip(".") for d in entry["domains"])
            if not domains or any(not d or "/" in d or ":" in d for d in domains):
                raise ValueError(f"Invalid domains for approved source: {entry.get('name')!r}")
            sources.append(
                ApprovedSource(
                    name=entry["name"],
                    country=entry["country"],
                    region=entry["region"],
                    tier=int(entry["tier"]),
                    domains=domains,
                )
            )
        return cls(sources)

    def match_host(self, host: str | None) -> ApprovedSource | None:
        if not host:
            return None
        host = host.lower().rstrip(".")
        for source in self.sources:
            for domain in source.domains:
                if host == domain or host.endswith("." + domain):
                    return source
        return None

    def match(self, url: str) -> ApprovedSource | None:
        try:
            return self.match_host(urlsplit(url).hostname)
        except ValueError:
            return None
