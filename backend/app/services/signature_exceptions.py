class SignatureError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)


class SignatureNotFoundError(SignatureError):
    def __init__(self, message: str = "Intento de firma no encontrado"):
        super().__init__(message)


class SignatureNotAllowedError(SignatureError):
    def __init__(self, message: str = "No autorizado para firmar este paso"):
        super().__init__(message)


class SignatureConcurrencyError(SignatureError):
    def __init__(self, message: str = "Conflicto de concurrencia (revnum o verlock obsoleto)"):
        super().__init__(message)


class SignatureAlreadyCompletedError(SignatureError):
    def __init__(self, message: str = "El participante ya completo la firma"):
        super().__init__(message)


class SignatureActiveAttemptError(SignatureError):
    def __init__(self, message: str = "Existe un intento de firma activo"):
        super().__init__(message)


class SignaturePayloadError(SignatureError):
    def __init__(self, message: str = "Payload de firma invalido o corrupto"):
        super().__init__(message)


class SignatureUploadError(SignatureError):
    def __init__(self, message: str = "No fue posible publicar en Alfresco"):
        super().__init__(message)


class SignatureStateError(SignatureError):
    def __init__(self, message: str = "Transicion de estado de firma invalida"):
        super().__init__(message)


class SignaturePlacementError(SignatureError):
    def __init__(self, message: str = "No hay espacio suficiente para ubicar la firma"):
        super().__init__(message)


class SignatureIntegrityError(SignatureError):
    def __init__(self, message: str = "Hash remoto no coincide con hash local"):
        super().__init__(message)


class SignatureVersionConflictError(SignatureError):
    def __init__(self, message: str = "Conflicto en la version fuente del documento"):
        super().__init__(message)


class SignatureRecoveryRequiredError(SignatureError):
    def __init__(self, message: str = "Se requiere reconciliacion de intento"):
        super().__init__(message)


class SignaturePublicationError(SignatureError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        status_code: int = 409,
        operation_id: str | None = None,
        source_version: str | None = None,
        publication_status: str = "BLOCKED",
    ):
        self.code = code
        self.status_code = status_code
        self.operation_id = operation_id
        self.source_version = source_version
        self.publication_status = publication_status
        super().__init__(message)


class SignatureArtifactGoneError(SignaturePublicationError):
    def __init__(
        self,
        message: str = "El PDF acumulativo ya no esta disponible",
        *,
        operation_id: str | None = None,
        source_version: str | None = None,
    ):
        super().__init__(
            message,
            code="ACCUMULATED_ARTIFACT_GONE",
            status_code=410,
            operation_id=operation_id,
            source_version=source_version,
        )


class SignatureWriteDisabledError(SignaturePublicationError):
    def __init__(
        self,
        message: str = "Publicacion deshabilitada en este entorno",
        *,
        operation_id: str | None = None,
        source_version: str | None = None,
    ):
        super().__init__(
            message,
            code="ALFRESCO_WRITE_DISABLED",
            status_code=409,
            operation_id=operation_id,
            source_version=source_version,
        )
