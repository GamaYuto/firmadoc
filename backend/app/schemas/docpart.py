from typing import Optional, Dict, Any
from pydantic import BaseModel, ConfigDict, Field, field_validator
from datetime import datetime

class DocPartCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dpasid: int
    usrid: str = Field(..., min_length=1)
    orden: int = Field(..., gt=0)
    obliga: bool

    @field_validator('usrid')
    def validate_usrid(cls, v):
        if not v or not v.strip():
            raise ValueError('usrid no puede estar vacío')
        return v.strip()

class DocPartRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    parid: int
    dpasid: int
    usrid: str
    nomcom: str
    correo: str
    rolpro: Optional[str] = None
    orden: int
    obliga: bool
    estado: str
    motivo: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    
    fecdis: Optional[datetime] = None
    fecini: Optional[datetime] = None
    fecfin: Optional[datetime] = None
    
    verlock: int
    usrcre: str
    feccre: datetime
    usrmod: Optional[str] = None
    fecmod: Optional[datetime] = None

class DocPartTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_verlock: int = Field(..., ge=1)
    estado: str
    motivo: Optional[str] = None
    fecdis: Optional[datetime] = None
    fecini: Optional[datetime] = None
    fecfin: Optional[datetime] = None
    clear_fecfin: bool = False
    usrmod: str

class DocPartComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_verlock: int = Field(..., ge=1)
    result: Optional[Dict[str, Any]] = None

class DocPartReject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_verlock: int = Field(..., ge=1)
    motivo: str = Field(..., min_length=1)

class DocPartOmit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_verlock: int = Field(..., ge=1)
    motivo: str = Field(..., min_length=1)

class DocPartCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_verlock: int = Field(..., ge=1)
    motivo: str = Field(..., min_length=1)
