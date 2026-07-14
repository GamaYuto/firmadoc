from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    PROJECT_NAME: str = "FirmaDoc"
    DATABASE_URL: str

    ALFRESCO_BASE_URL: str
    ALFRESCO_API_URL: str
    ALFRESCO_USER: str
    ALFRESCO_PASSWORD: str
    ALFRESCO_TIMEOUT_SECONDS: int = 30
    ALFRESCO_VERIFY_SSL: bool = True
    ALFRESCO_MAX_DOWNLOAD_MB: int = 10

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
