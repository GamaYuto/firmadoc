from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from app.core.config import settings


class TemporaryArtifactService:
    def __init__(
        self,
        base_dir: Optional[Path | str] = None,
        ttl_minutes: Optional[int] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.base_dir = Path(base_dir or settings.FIRMADOC_TMP_DIR)
        self.ttl_minutes = settings.FIRMADOC_TMP_TTL_MINUTES if ttl_minutes is None else ttl_minutes
        self.clock = clock or time.time

    def ensure_base_dir(self) -> Path:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        return self.base_dir

    def _is_within_base_dir(self, candidate: Path) -> bool:
        base = self.base_dir.resolve(strict=False)
        resolved = candidate.resolve(strict=False)
        return resolved == base or base in resolved.parents

    def cleanup_expired(self) -> None:
        if not self.base_dir.exists():
            return

        cutoff = self.clock() - (self.ttl_minutes * 60)
        for candidate in self.base_dir.iterdir():
            try:
                if not candidate.is_file() or candidate.is_symlink():
                    continue
                if candidate.stat().st_mtime < cutoff:
                    candidate.unlink(missing_ok=True)
            except OSError:
                continue

    def create_path(self, prefix: str = "docfirma-", suffix: str = ".pdf") -> Path:
        self.ensure_base_dir()
        self.cleanup_expired()

        for _ in range(8):
            candidate = self.base_dir / f"{prefix}{uuid.uuid4()}{suffix}"
            try:
                fd = os.open(str(candidate), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                continue
            except OSError:
                candidate.touch(exist_ok=False)
                fd = os.open(str(candidate), os.O_WRONLY)
            try:
                os.close(fd)
            finally:
                try:
                    os.chmod(candidate, 0o600)
                except OSError:
                    pass
            return candidate

        raise RuntimeError("No fue posible crear un artefacto temporal unico")

    def write_bytes(self, content: bytes, prefix: str = "docfirma-", suffix: str = ".pdf") -> Path:
        path = self.create_path(prefix=prefix, suffix=suffix)
        try:
            path.write_bytes(content)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        except Exception:
            self.cleanup_path(path)
            raise
        return path

    def find_matching_paths(self, prefix: str = "", suffix: str = "") -> list[Path]:
        if not self.base_dir.exists():
            return []

        matches: list[Path] = []
        for candidate in self.base_dir.iterdir():
            try:
                if not candidate.is_file() or candidate.is_symlink():
                    continue
                if prefix and not candidate.name.startswith(prefix):
                    continue
                if suffix and not candidate.name.endswith(suffix):
                    continue
                if not self._is_within_base_dir(candidate):
                    continue
                matches.append(candidate)
            except OSError:
                continue

        def sort_key(path: Path) -> tuple[float, str]:
            try:
                return (path.stat().st_mtime, path.name)
            except OSError:
                return (0.0, path.name)

        return sorted(matches, key=sort_key, reverse=True)

    def find_latest_path(self, prefix: str = "", suffix: str = "") -> Path | None:
        matches = self.find_matching_paths(prefix=prefix, suffix=suffix)
        return matches[0] if matches else None

    def cleanup_path(self, path: Path | str | None) -> None:
        if not path:
            return
        candidate = Path(path)
        if not self._is_within_base_dir(candidate):
            return
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass


temporary_artifact_service = TemporaryArtifactService()
