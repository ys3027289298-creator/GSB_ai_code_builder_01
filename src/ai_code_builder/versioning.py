"""Lightweight version-tracking store.

This is *not* a full VCS. It's a content-addressed log of file revisions
that supports:

* Recording snapshots of arbitrary text files.
* Listing the version history per file.
* Restoring a previous version.
* Producing a unified diff between two versions.

State is stored in a single JSON file plus a ``blobs/`` directory of
content-addressed snapshots (sha256). The store is process-safe enough
for single-user CLI workflows; concurrent writers are not supported.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Version:
    file: str
    version: int
    sha256: str
    bytes: int
    created_at: float
    message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class VersionStore:
    """Tracks file revisions in a JSON-backed log."""

    INDEX_NAME = "index.json"
    BLOB_DIR = "blobs"

    def __init__(self, root: Path | str = ".acb_versions") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / self.BLOB_DIR).mkdir(exist_ok=True)
        self._index_path = self.root / self.INDEX_NAME
        if not self._index_path.exists():
            self._write_index({"files": {}})

    # ------------------------------------------------------------------

    def commit(self, file: str, content: str, message: str = "") -> Version:
        """Record a new version for ``file`` with the given content."""
        if not file:
            raise ValueError("file must be a non-empty string")
        index = self._read_index()
        files = index.setdefault("files", {})
        history: list[dict] = files.setdefault(file, [])

        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if history and history[-1]["sha256"] == digest:
            # Idempotent: don't record an identical snapshot twice.
            return Version(**history[-1])

        blob_path = self.root / self.BLOB_DIR / digest
        if not blob_path.exists():
            blob_path.write_text(content, encoding="utf-8")

        version = Version(
            file=file,
            version=len(history) + 1,
            sha256=digest,
            bytes=len(content.encode("utf-8")),
            created_at=time.time(),
            message=message,
        )
        history.append(version.to_dict())
        self._write_index(index)
        return version

    def history(self, file: str) -> list[Version]:
        index = self._read_index()
        return [Version(**v) for v in index.get("files", {}).get(file, [])]

    def list_files(self) -> list[str]:
        return sorted(self._read_index().get("files", {}).keys())

    def read(self, file: str, version: int) -> str:
        v = self._lookup(file, version)
        return (self.root / self.BLOB_DIR / v.sha256).read_text(encoding="utf-8")

    def restore(self, file: str, version: int, target: Path | str) -> Path:
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.read(file, version), encoding="utf-8")
        return target

    def diff(self, file: str, a: int, b: int) -> str:
        text_a = self.read(file, a).splitlines(keepends=True)
        text_b = self.read(file, b).splitlines(keepends=True)
        return "".join(
            difflib.unified_diff(
                text_a,
                text_b,
                fromfile=f"{file}@v{a}",
                tofile=f"{file}@v{b}",
            )
        )

    # ------------------------------------------------------------------

    def _lookup(self, file: str, version: int) -> Version:
        for v in self.history(file):
            if v.version == version:
                return v
        raise KeyError(f"{file}@v{version} not found")

    def _read_index(self) -> dict:
        with self._index_path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def _write_index(self, data: dict) -> None:
        tmp = self._index_path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.replace(tmp, self._index_path)
