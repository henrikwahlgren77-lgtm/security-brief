"""Versioned prompt files: ``prompts/<role>/v<N>.md``.

Each run records the version and SHA-256 of the exact text it used, so a report
can be traced back to the prompt that produced it. A prompt file is never
edited in place after use: improve it by adding the next version.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from .providers import AnalysisRole

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "prompts"
_VERSION = re.compile(r"^v(\d+)$")


class PromptError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Prompt:
    role: AnalysisRole
    version: str
    text: str
    sha256: str


class PromptLibrary:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root else DEFAULT_ROOT

    def versions(self, role: AnalysisRole) -> list[str]:
        directory = self.root / AnalysisRole(role).value
        if not directory.is_dir():
            return []
        found = [p.stem for p in directory.glob("v*.md") if _VERSION.match(p.stem)]
        return sorted(found, key=lambda v: int(v[1:]))

    def load(self, role: AnalysisRole, version: str | None = None) -> Prompt:
        role = AnalysisRole(role)
        available = self.versions(role)
        if not available:
            raise PromptError(f"No prompt files found for role '{role.value}'")
        chosen = version or available[-1]
        # The version comes from configuration: accept only the exact v<N> form.
        if not _VERSION.match(chosen) or chosen not in available:
            raise PromptError(
                f"Prompt version '{chosen}' does not exist for role '{role.value}'. Available: {', '.join(available)}"
            )
        text = (self.root / role.value / f"{chosen}.md").read_text(encoding="utf-8").strip()
        if not text:
            raise PromptError(f"Prompt file {role.value}/{chosen}.md is empty")
        return Prompt(role=role, version=chosen, text=text, sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())
