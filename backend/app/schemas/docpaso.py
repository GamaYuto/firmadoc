from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Optional, Any, Dict, List
from datetime import datetime

class DocPasoCreate(BaseModel):
    docid: int
    pasid: int

class DocPasoRead(BaseModel):
    dpasid: int
    docid: int
    pasid: int
    orden: int
    estado: str
    
    fecdis: Optional[datetime] = None
    fecini: Optional[datetime] = None
    feclim: Optional[datetime] = None
    fecfin: Optional[datetime] = None
    
    result: Optional[Dict[str, Any]] = None
    motivo: Optional[str] = None
    
    pastip: str
    rolreq: str
    obliga: bool
    plazo: Optional[int] = None
    config: Optional[Dict[str, Any]] = None
    
    verlock: int
    activo: bool
    usrcre: str
    feccre: datetime
    usrmod: Optional[str] = None
    fecmod: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

class DocPasoList(BaseModel):
    items: List[DocPasoRead]

class DocPasoTransition(BaseModel):
    estado_destino: str
    motivo: Optional[str] = Field(default=None, max_length=500)
    verlock: int

    @field_validator('motivo')
    def validar_motivo_vacio(cls, v):
        if v is not None and not str(v).strip():
            raise ValueError("El motivo no puede estar vacío")
        return str(v).strip() if v is not None else v

class DocPasoResult(BaseModel):
    result: Dict[str, Any]
    verlock: int

class DocPasoReactivate(BaseModel):
    motivo: str = Field(..., max_length=500)
    verlock: int

    @field_validator('motivo')
    def validar_motivo_vacio(cls, v):
        if not str(v).strip():
            raise ValueError("El motivo no puede estar vacío")
        return str(v).strip()
