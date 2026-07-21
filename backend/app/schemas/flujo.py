from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import List, Optional, Any, Dict
from datetime import datetime

class PasoBase(BaseModel):
    pascod: str = Field(..., min_length=1, max_length=30)
    pasnom: str = Field(..., min_length=1, max_length=100)
    pastip: str = Field(...)
    orden: int = Field(..., gt=0)
    rolreq: str = Field(..., min_length=1, max_length=40)
    obliga: bool = Field(default=True)
    plazo: Optional[int] = Field(default=None, gt=0)
    config: Optional[Dict[str, Any]] = Field(default=None)

    @field_validator('pastip')
    def validar_tipo(cls, v):
        permitidos = ['DILIGENCIAR', 'REVISAR', 'APROBAR', 'FIRMAR', 'ATESTIGUAR', 'CERRAR', 'PUBLICAR']
        if v not in permitidos:
            raise ValueError(f"Tipo de paso inválido. Permitidos: {permitidos}")
        return v

    @field_validator('pascod', 'pasnom', 'rolreq')
    def validar_cadenas_vacias(cls, v):
        if not str(v).strip():
            raise ValueError("Las cadenas no pueden estar vacías")
        return str(v).strip()

class PasoCreate(PasoBase):
    pass

class PasoUpdate(BaseModel):
    pasnom: Optional[str] = Field(None, min_length=1, max_length=100)
    rolreq: Optional[str] = Field(None, min_length=1, max_length=40)
    obliga: Optional[bool] = Field(None)
    plazo: Optional[int] = Field(None, gt=0)
    config: Optional[Dict[str, Any]] = Field(None)

    @field_validator('pasnom', 'rolreq')
    def validar_cadenas_vacias(cls, v):
        if v is not None and not str(v).strip():
            raise ValueError("Las cadenas no pueden estar vacías")
        return str(v).strip() if v is not None else v

class PasoRead(PasoBase):
    pasid: int
    fluid: int
    activo: bool
    usrcre: str
    feccre: datetime
    usrmod: Optional[str] = None
    fecmod: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

class PasoList(BaseModel):
    items: List[PasoRead]

class FlujoBase(BaseModel):
    flucod: str = Field(..., min_length=1, max_length=30)
    flunom: str = Field(..., min_length=1, max_length=150)

    @field_validator('flucod', 'flunom')
    def validar_cadenas_vacias(cls, v):
        if not str(v).strip():
            raise ValueError("Las cadenas no pueden estar vacías")
        return str(v).strip()

class FlujoCreate(FlujoBase):
    usrcre: str = Field(..., min_length=1)

class FlujoUpdate(BaseModel):
    flunom: Optional[str] = Field(None, min_length=1, max_length=150)
    usrmod: str = Field(..., min_length=1)

    @field_validator('flunom', 'usrmod')
    def validar_cadenas_vacias(cls, v):
        if v is not None and not str(v).strip():
            raise ValueError("Las cadenas no pueden estar vacías")
        return str(v).strip() if v is not None else v

class FlujoRead(FlujoBase):
    fluid: int
    fluver: int
    estado: str
    activo: bool
    usrcre: str
    feccre: datetime
    usrmod: Optional[str] = None
    fecmod: Optional[datetime] = None
    pasos: List[PasoRead] = []

    model_config = ConfigDict(from_attributes=True)

class FlujoList(BaseModel):
    items: List[FlujoRead]
