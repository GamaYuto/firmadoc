from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, UUID4

class NodeMetadata(BaseModel):
    node_id: UUID4
    name: str
    node_type: str
    is_file: bool
    mime_type: Optional[str] = None
    size_bytes: int = 0
    modified_at: Optional[datetime] = None
    modified_by: Optional[str] = None
    version_label: Optional[str] = None
    parent_id: Optional[UUID4] = None


@dataclass(frozen=True, slots=True)
class AlfrescoRepositorySnapshot:
    repository_id: str
    edition: str
    version_major: int
    version_minor: int
    version_patch: int
    version_hotfix: int
    version_schema: int
    version_label: str
    version_display: str
    is_read_only: bool


@dataclass(frozen=True, slots=True)
class AlfrescoNodeSnapshot:
    node_id: str
    name: str
    node_type: str
    mime_type: Optional[str]
    size_bytes: int
    modified_at: Optional[datetime]
    version_label: Optional[str]
    etag: Optional[str] = None
    is_file: bool = True
    path: Optional[str] = None
    modified_by: Optional[str] = None


@dataclass(frozen=True, slots=True)
class AlfrescoVersionSnapshot:
    node_id: str
    version_id: str
    comment: Optional[str]
    created_at: Optional[datetime]
    modifier: Optional[str]
    etag: Optional[str] = None


@dataclass(frozen=True, slots=True)
class AlfrescoDownloadedArtifact:
    node_id: str
    version_id: Optional[str]
    path: Path
    sha256: str
    size_bytes: int
    mime_type: str


@dataclass(frozen=True, slots=True)
class AlfrescoUploadResult:
    node_id: str
    version_id: Optional[str]
    status_code: int
    etag: Optional[str]
    remote_message: str
