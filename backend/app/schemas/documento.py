from pydantic import BaseModel, ConfigDict, Field, UUID4
from typing import Optional
from datetime import datetime
from app.models.docfir import EstadoDoc

class DocFirCreate(BaseModel):
    node_id: UUID4

class DocFirRead(BaseModel):
    docid: int
    nodid: str
    docnom: str
    mimtip: str
    tamano: int
    verini: str
    verfin: Optional[str] = None
    estado: EstadoDoc
    hasori: str
    hasfir: Optional[str] = None
    usrcre: str
    feccre: datetime
    usrmod: Optional[str] = None
    fecmod: Optional[datetime] = None
    activo: bool

    model_config = ConfigDict(from_attributes=True)

class DocFirList(BaseModel):
    items: list[DocFirRead]
    total: int

class DocFirCancel(BaseModel):
    motivo: str = Field(..., min_length=1, max_length=500)
