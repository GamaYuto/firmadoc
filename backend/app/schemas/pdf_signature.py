from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


@dataclass(frozen=True, slots=True)
class PdfValidationResult:
    path: Path
    sha256: str
    size_bytes: int
    page_count: int
    page_sizes: tuple[tuple[float, float], ...]


@dataclass(frozen=True, slots=True)
class PngValidationResult:
    path: Path
    sha256: str
    size_bytes: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class GeneratedPdfArtifact:
    firid: int
    opeid: UUID
    path: Path
    sha256: str
    size_bytes: int
    page_count: int

    def cleanup(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass
