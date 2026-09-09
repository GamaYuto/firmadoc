from __future__ import annotations

import os
import stat
import time
from pathlib import Path

from app.services.temporary_artifact_service import TemporaryArtifactService


def test_temporary_artifact_service_creates_uuid_named_file_and_sets_permissions(tmp_path):
    base_dir = tmp_path / "artifacts"
    service = TemporaryArtifactService(base_dir=base_dir)

    path = service.write_bytes(b"artifact", prefix="docfirma-", suffix=".pdf")

    try:
        assert path.parent == base_dir
        assert path.exists()
        assert path.name.startswith("docfirma-")
        uuid_part = path.stem.removeprefix("docfirma-")
        assert len(uuid_part) >= 32
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode & 0o600 == 0o600
    finally:
        service.cleanup_path(path)


def test_temporary_artifact_service_cleans_expired_files_with_controlled_clock(tmp_path):
    base_dir = tmp_path / "artifacts"
    now = 1_000_000.0
    service = TemporaryArtifactService(base_dir=base_dir, ttl_minutes=30, clock=lambda: now)

    old_path = service.write_bytes(b"old", prefix="old-", suffix=".tmp")
    fresh_path = service.write_bytes(b"fresh", prefix="fresh-", suffix=".tmp")

    old_epoch = now - (31 * 60)
    fresh_epoch = now - (10 * 60)
    os.utime(old_path, (old_epoch, old_epoch))
    os.utime(fresh_path, (fresh_epoch, fresh_epoch))

    service.cleanup_expired()

    assert not old_path.exists()
    assert fresh_path.exists()

    service.cleanup_path(fresh_path)


def test_temporary_artifact_service_ignores_foreign_paths(tmp_path):
    base_dir = tmp_path / "artifacts"
    foreign_dir = tmp_path / "foreign"
    foreign_dir.mkdir()
    foreign_path = foreign_dir / "external.pdf"
    foreign_path.write_bytes(b"external")

    service = TemporaryArtifactService(base_dir=base_dir)
    service.cleanup_path(foreign_path)

    assert foreign_path.exists()
