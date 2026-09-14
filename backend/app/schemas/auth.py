from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(..., min_length=1, max_length=60, description="Identificador único del usuario")
    password: Optional[str] = Field(default=None, description="Contraseña opcional en entorno de prueba")


class SessionResponse(BaseModel):
    user_id: str
    nombre_completo: str
    correo: str
    roles: List[str]
    is_authenticated: bool
    csrf_token: Optional[str] = None
