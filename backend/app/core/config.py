from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    PROJECT_NAME: str = "FirmaDoc"
    DATABASE_URL: str

    ALFRESCO_BASE_URL: str
    ALFRESCO_API_URL: Optional[str] = None
    ALFRESCO_USER: Optional[str] = None
    ALFRESCO_PASSWORD: str
    ALFRESCO_TIMEOUT_SECONDS: int = 30
    ALFRESCO_VERIFY_SSL: bool = True
    ALFRESCO_MAX_DOWNLOAD_MB: int = 10
    ALFRESCO_API_PATH: Optional[str] = None
    ALFRESCO_USERNAME: Optional[str] = None
    ALFRESCO_CA_BUNDLE: Optional[str] = None
    ALFRESCO_CONNECT_TIMEOUT: int = 5
    ALFRESCO_READ_TIMEOUT: int = 30
    ALFRESCO_WRITE_TIMEOUT: int = 60
    ALFRESCO_MAX_DOWNLOAD_SIZE: int = 52428800
    ALFRESCO_MAX_HISTORY_PAGES: int = 100
    ALFRESCO_HISTORY_PAGE_SIZE: int = 100

    FIRMADOC_ALFRESCO_EXPECTED_HOST: str = "alfresco-lab.test"
    FIRMADOC_ALFRESCO_WRITE_ENABLED: bool = False
    FIRMADOC_LAB_IDENTITY_ENABLED: bool = True
    FIRMADOC_ALFRESCO_TEST_NODE_ID: Optional[str] = None
    FIRMADOC_ALFRESCO_TEST_EXPECTED_NAME: Optional[str] = None
    FIRMADOC_ALFRESCO_TEST_EXPECTED_PATH: Optional[str] = None
    FIRMADOC_ALFRESCO_TEST_EXPECTED_MIMETYPE: str = "application/pdf"
    FIRMADOC_ALFRESCO_MAJOR_VERSION: bool = False
    FIRMADOC_RECONCILE_MIN_CHECKS: int = 3
    FIRMADOC_RECONCILE_WAIT_SECONDS: int = 30

    FIRMADOC_MAX_PDF_SIZE: int = 52428800
    FIRMADOC_MAX_PDF_PAGES: int = 500
    FIRMADOC_MAX_PAGE_WIDTH: int = 5000
    FIRMADOC_MAX_PAGE_HEIGHT: int = 5000
    FIRMADOC_MAX_PNG_SIZE: int = 512000
    FIRMADOC_MAX_IMAGE_WIDTH: int = 4096
    FIRMADOC_MAX_IMAGE_HEIGHT: int = 4096
    FIRMADOC_TMP_TTL_MINUTES: int = 30
    FIRMADOC_TMP_DIR: str = "tmp/firmadoc"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
