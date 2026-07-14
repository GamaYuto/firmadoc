from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime

class CampoBase(BaseModel):
    camcod: str = Field(..., min_length=1, max_length=30)
    camnom: str = Field(..., min_length=1, max_length=100)
    camtip: Literal['TEXTO', 'TEXLAR', 'CASILLA', 'OPCION', 'FIRMA', 'SELLO', 'FECHA', 'NOMBRE', 'TIPDOC', 'NUMDOC']
    pagina: int = Field(..., gt=0)
    posx: float = Field(..., ge=0, le=1)
    posy: float = Field(..., ge=0, le=1)
    ancho: float = Field(..., gt=0, le=1)
    alto: float = Field(..., gt=0, le=1)
    obliga: bool = False
    orden: int = Field(..., gt=0)
    config: Optional[Dict[str, Any]] = None

    @model_validator(mode='after')
    def validate_coordinates(self):
        if self.posx + self.ancho > 1.0001: # allow slight float precision overlap
            raise ValueError("posx + ancho excede los límites de la página")
        if self.posy + self.alto > 1.0001:
            raise ValueError("posy + alto excede los límites de la página")
        return self

class CampoCreate(CampoBase):
    pass

class CampoUpdate(BaseModel):
    camnom: Optional[str] = Field(None, max_length=100)
    pagina: Optional[int] = Field(None, gt=0)
    posx: Optional[float] = Field(None, ge=0, le=1)
    posy: Optional[float] = Field(None, ge=0, le=1)
    ancho: Optional[float] = Field(None, gt=0, le=1)
    alto: Optional[float] = Field(None, gt=0, le=1)
    obliga: Optional[bool] = None
    orden: Optional[int] = Field(None, gt=0)
    config: Optional[Dict[str, Any]] = None

class CampoRead(CampoBase):
    camid: int
    tplid: int
    activo: bool
    usrcre: str
    feccre: datetime
    usrmod: Optional[str] = None
    fecmod: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

class CampoList(BaseModel):
    items: List[CampoRead]
    total: int
