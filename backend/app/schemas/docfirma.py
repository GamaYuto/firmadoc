from pydantic import BaseModel, Field, field_validator
from typing import Literal, List, Optional
from enum import Enum

class ResultFase(str, Enum):
    RESERVA = "RESERVA"
    GENERACION = "GENERACION"
    REVALIDACION = "REVALIDACION"
    PUBLICACION = "PUBLICACION"
    VERIFICACION = "VERIFICACION"
    FINALIZACION = "FINALIZACION"
    RECONCILIACION = "RECONCILIACION"

class ResultFlag(str, Enum):
    ALFRESCO_OK = "ALFRESCO_OK"
    HASH_MATCH = "HASH_MATCH"
    HASH_MISMATCH = "HASH_MISMATCH"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    TIMEOUT = "TIMEOUT"
    RECOVERY_PENDING = "RECOVERY_PENDING"

class ResultContract(BaseModel):
    schema_ver: Literal[1] = 1
    fase: ResultFase
    remcod: Optional[int] = None
    remmsg: Optional[str] = Field(None, max_length=200)
    recint: int = Field(default=0, ge=0)
    verchk: bool
    haschk: bool
    flags: List[ResultFlag]

    model_config = {
        "extra": "forbid"
    }

    @field_validator('remmsg')
    @classmethod
    def sanitize_remmsg(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        # Basic sanitization of potential injection or carriage returns
        return v.strip().replace("\r", "").replace("\n", " ").strip()
