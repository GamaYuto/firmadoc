from pydantic import BaseModel, UUID4
from datetime import datetime
from typing import Optional

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
