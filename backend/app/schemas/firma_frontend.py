from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PageInfo(BaseModel):
    page: int
    width: float
    height: float
    rotation: int = 0


class PreparationParticipant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    usrid: str = Field(..., min_length=1, max_length=60)
    orden: int = Field(..., gt=0)
    obliga: bool = True

    @field_validator("usrid")
    @classmethod
    def normalize_usrid(cls, value: str) -> str:
        value = value.strip().lower()
        if not value:
            raise ValueError("usrid no puede estar vacio")
        return value


class PreparationPosition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pagina: int = Field(..., gt=0)
    posx: Decimal = Field(..., ge=0)
    posy: Decimal = Field(..., ge=0)
    ancho: Decimal = Field(..., gt=0)
    alto: Decimal = Field(..., gt=0)
    rotaci: int = Field(default=0)
    orden: int = Field(..., gt=0)
    tipfir: Literal["MANUSCRITA", "INTERNA"]
    usrid: str = Field(..., min_length=1, max_length=60)

    @field_validator("rotaci")
    @classmethod
    def validate_rotation(cls, value: int) -> int:
        if value not in (0, 90, 180, 270):
            raise ValueError("rotaci debe ser 0, 90, 180 o 270")
        return value

    @field_validator("usrid")
    @classmethod
    def normalize_position_user(cls, value: str) -> str:
        value = value.strip().lower()
        if not value:
            raise ValueError("usrid no puede estar vacio")
        return value


class PreparationDraftSave(BaseModel):
    model_config = ConfigDict(extra="forbid")

    participant: Optional[PreparationParticipant] = None
    participants: list[PreparationParticipant] = Field(default_factory=list, max_length=20)
    positions: list[PreparationPosition] = Field(..., min_length=1, max_length=20)

    @model_validator(mode="after")
    def ensure_participants(self) -> "PreparationDraftSave":
        if self.participant and not self.participants:
            self.participants = [self.participant]
        if not self.participants:
            raise ValueError("Debe indicar al menos un participante")
        return self


class ParticipantRead(BaseModel):
    parid: int
    usrid: str
    nomcom: str
    correo: str
    rolpro: Optional[str] = None
    orden: int
    estado: str
    verlock: int


class PositionRead(BaseModel):
    pagina: int
    posx: float
    posy: float
    ancho: float
    alto: float
    rotaci: int
    orden: int
    tipfir: Literal["MANUSCRITA", "INTERNA"]
    usrid: str
    saved: bool = False


class PreparationRead(BaseModel):
    docid: int
    node_id: str
    document_name: str
    version: str
    status: str
    hash_original: str
    pages: list[PageInfo]
    participants: list[ParticipantRead]
    positions: list[PositionRead]
    active_firid: Optional[int] = None


class SendToSignatureResponse(BaseModel):
    firid: int
    docid: int
    parid: int
    status: str
    positions: list[PositionRead]


class PendingItem(BaseModel):
    firid: int
    docid: int
    parid: int
    document_name: str
    etapa: str
    rol: str
    fecha: Optional[datetime] = None
    estado: str
    tipfir: str


class PendingList(BaseModel):
    items: list[PendingItem]
    total: int


class SignatureDetail(BaseModel):
    firid: int
    docid: int
    parid: int
    node_id: str
    document_name: str
    document_status: str
    signer_user: str
    signer_name: str
    signer_role: Optional[str] = None
    tipfir: Literal["MANUSCRITA", "INTERNA"]
    estado: str
    revnum: int
    participant_verlock: int
    positions: list[PositionRead]
    max_png_size: int


class HandwrittenSignatureConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    png_data_url: str = Field(..., min_length=32, max_length=900_000)

    @field_validator("png_data_url")
    @classmethod
    def validate_data_url(cls, value: str) -> str:
        if not value.startswith("data:image/png;base64,"):
            raise ValueError("La firma debe enviarse como PNG en data URL")
        return value


class InternalSignatureConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool


class SignatureResult(BaseModel):
    firid: int
    docid: int
    status: str
    document_name: Optional[str] = None
    document_status: Optional[str] = None
    completed_signatures: int = 0
    total_signatures: int = 0
    original_version: str
    final_version: Optional[str] = None
    final_hash: Optional[str] = None
    final_hash_short: Optional[str] = None
    date: datetime
    alfresco_publication: Literal["DISABLED", "PENDING", "PUBLISHED"] = "DISABLED"
    alfresco_write_enabled: bool = False
    can_publish_alfresco: bool = False
    message: str


class PublicationResponse(BaseModel):
    status: Literal["BLOCKED", "PENDING", "PUBLISHED", "RECOVERY_REQUIRED", "CONFLICT"]
    operation_id: str
    source_version: str
    publication_status: str
    code: Optional[str] = None
    message: str
    final_version: Optional[str] = None
    final_hash_short: Optional[str] = None


class QrSessionCreateResponse(BaseModel):
    sesid: int
    token: str
    qr_url: str
    expires_in: int
    expires_at: str


class QrSessionStatusResponse(BaseModel):
    sesid: int
    estado: str


class MobileSessionDetail(BaseModel):
    token: str
    docnom: str
    usrid: str
    tipfir: str
    docid: int
    firid: int


class MobileSignatureConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(..., min_length=16, max_length=100)
    png_data_url: str = Field(..., min_length=32, max_length=900_000)

    @field_validator("png_data_url")
    @classmethod
    def validate_data_url(cls, value: str) -> str:
        if not value.startswith("data:image/png;base64,"):
            raise ValueError("La firma debe enviarse como PNG en data URL")
        return value
