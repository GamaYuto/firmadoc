class StepConcurrencyError(Exception):
    """
    Excepción de dominio para errores de concurrencia al usar el verlock optimista.
    """
    def __init__(self, message: str = "Conflicto de concurrencia detectado. El registro ha sido modificado por otra transacción."):
        self.message = message
        super().__init__(self.message)
