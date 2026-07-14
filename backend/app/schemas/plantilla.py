from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List
from datetime import datetime

class PlantillaBase(BaseModel):
    tplcod: str = Field(..., min_length=1, max_length=30)
    tplnom: str = Field(..., min_length=1, max_length=150)
    nodid: Optional[str] = Field(None, max_length=64)
    numpag: int = Field(..., gt=0)

class PlantillaCreate(PlantillaBase):
    pass

class PlantillaUpdate(BaseModel):
    tplnom: Optional[str] = Field(None, max_length=150)
    nodid: Optional[str] = Field(None, max_length=64)
    numpag: Optional[int] = Field(None, gt=0)

class PlantillaRead(PlantillaBase):
    tplid: int
    tplver: int
    estado: str
    activo: bool
    usrcre: str
    feccre: datetime
    usrmod: Optional[str] = None
    fecmod: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

class PlantillaList(BaseModel):
    items: List[PlantillaRead]
    total: int
