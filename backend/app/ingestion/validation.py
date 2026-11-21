"""Upload validation (ADR 0008).

The file type is decided by *content*, never by the client's filename or Content-Type header.
Checks, in order:
  1. Stream to a temp file with a hard size cap, hashing (SHA-256) as we go.
  2. Magic-byte detection: PDF, PNG, JPEG, OOXML (ZIP) or legacy/encrypted OLE.
  3. Per-format safety checks:
     - PDF: opens with PDFium, rejects encrypted/password-protected, enforces page limit.
     - OOXML: identifies DOCX/XLSX/PPTX from the package's parts; zip-bomb guards on entry
       count, total uncompressed size and compression ratio; rejects path-traversal entries.
     - Images: decoded header with Pillow's decompression-bomb guard and a pixel limit.
  4. A declared extension that contradicts the detected type is rejected (a `.pdf` that is
     really a ZIP is suspicious, not a convenience).
"""

import enum
import hashlib
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO

import pypdfium2 as pdfium
from PIL import Image

from app.core.errors import AppError

CHUNK = 1024 * 1024
MAX_ZIP_ENTRIES = 10_000
MAX_ZIP_UNCOMPRESSED = 1024 * 1024 * 1024  # 1 GiB expanded
MAX_ZIP_RATIO = 100  # per-entry compression ratio
MAX_IMAGE_PIXELS = 100_000_000  # ~100 MP


class DocumentKind(enum.StrEnum):
    PDF = "pdf"
    PNG = "png"
    JPEG = "jpeg"
    DOCX = "docx"
    XLSX = "xlsx"
    PPTX = "pptx"


MIME_TYPES: dict[DocumentKind, str] = {
    DocumentKind.PDF: "application/pdf",
    DocumentKind.PNG: "image/png",
    DocumentKind.JPEG: "image/jpeg",
    DocumentKind.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    DocumentKind.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    DocumentKind.PPTX: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

_EXTENSIONS: dict[str, DocumentKind] = {
    ".pdf": DocumentKind.PDF,
    ".png": DocumentKind.PNG,
    ".jpg": DocumentKind.JPEG,
    ".jpeg": DocumentKind.JPEG,
    ".docx": DocumentKind.DOCX,
    ".xlsx": DocumentKind.XLSX,
    ".pptx": DocumentKind.PPTX,
}

_OOXML_MAIN_PARTS: dict[str, DocumentKind] = {
    "word/document.xml": DocumentKind.DOCX,
    "xl/workbook.xml": DocumentKind.XLSX,
    "ppt/presentation.xml": DocumentKind.PPTX,
}


class UploadRejectedError(AppError):
    status_code = 415
    problem_type = "unsupported-document"
    title = "Unsupported or Unsafe Document"


class UploadTooLargeError(AppError):
    status_code = 413
    problem_type = "payload-too-large"
    title = "Payload Too Large"


@dataclass(slots=True)
class ValidatedUpload:
    path: Path
    sha256: str
    size_bytes: int
    kind: DocumentKind
    page_count: int | None
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def mime_type(self) -> str:
        return MIME_TYPES[self.kind]


def spool_to_disk(
    stream: BinaryIO, max_bytes: int, tmp_dir: Path | None = None
) -> tuple[Path, str, int]:
    """Copy `stream` to a temp file, enforcing `max_bytes` while streaming (never trusting
    Content-Length). Returns (path, sha256 hex, size)."""
    digest = hashlib.sha256()
    size = 0
    with tempfile.NamedTemporaryFile(dir=tmp_dir, prefix="upload-", delete=False) as out:
        path = Path(out.name)
        try:
            while chunk := stream.read(CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise UploadTooLargeError(
                        f"File exceeds the {max_bytes // (1024 * 1024)} MB limit."
                    )
                digest.update(chunk)
                out.write(chunk)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    if size == 0:
        path.unlink(missing_ok=True)
        raise UploadRejectedError("The file is empty.")
    return path, digest.hexdigest(), size


def sniff(head: bytes) -> str:
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "ole"  # legacy .doc/.xls/.ppt, or a password-encrypted OOXML container
    return "unknown"


def _check_pdf(path: Path, max_pages: int) -> tuple[int, dict[str, object]]:
    try:
        pdf = pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as exc:
        msg = str(exc).lower()
        if "password" in msg:
            raise UploadRejectedError(
                "Encrypted or password-protected PDFs are not supported."
            ) from exc
        raise UploadRejectedError("The PDF is corrupt or unreadable.") from exc
    try:
        pages = len(pdf)
        if pages == 0:
            raise UploadRejectedError("The PDF has no pages.")
        if pages > max_pages:
            raise UploadRejectedError(f"The PDF has {pages} pages; the limit is {max_pages}.")
        meta = {
            k: v for k, v in pdf.get_metadata_dict(skip_empty=True).items() if isinstance(v, str)
        }
        return pages, {"pdf_metadata": meta}
    finally:
        pdf.close()


def _check_ooxml(path: Path) -> DocumentKind:
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise UploadRejectedError("The file is not a valid Office document.") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise UploadRejectedError("The document package has too many parts.")
        total = 0
        for info in infos:
            name = PurePosixPath(info.filename)
            if name.is_absolute() or ".." in name.parts:
                raise UploadRejectedError("The document package contains unsafe paths.")
            total += info.file_size
            if info.compress_size and info.file_size / info.compress_size > MAX_ZIP_RATIO:
                raise UploadRejectedError("The document package looks like a compression bomb.")
        if total > MAX_ZIP_UNCOMPRESSED:
            raise UploadRejectedError("The document package expands beyond the allowed size.")
        names = {i.filename for i in infos}
        if "[Content_Types].xml" not in names:
            raise UploadRejectedError("ZIP archives are not supported; upload DOCX, XLSX or PPTX.")
        for part, kind in _OOXML_MAIN_PARTS.items():
            if part in names:
                return kind
    raise UploadRejectedError("Unrecognised Office document type.")


def _check_image(path: Path, kind: DocumentKind) -> dict[str, object]:
    try:
        with Image.open(path) as img:
            width, height = img.size
            if width * height > MAX_IMAGE_PIXELS:
                raise UploadRejectedError("The image resolution exceeds the allowed limit.")
            img.verify()  # structural integrity check without a full decode
    except UploadRejectedError:
        raise
    except (Image.DecompressionBombError, OSError, SyntaxError) as exc:
        raise UploadRejectedError(f"The {kind.value.upper()} image is corrupt or unsafe.") from exc
    return {"width": width, "height": height}


def validate_file(
    path: Path,
    *,
    sha256: str,
    size_bytes: int,
    original_filename: str,
    max_pages: int,
) -> ValidatedUpload:
    with path.open("rb") as fh:
        signature = sniff(fh.read(16))

    page_count: int | None = None
    metadata: dict[str, object] = {}
    match signature:
        case "pdf":
            kind = DocumentKind.PDF
            page_count, metadata = _check_pdf(path, max_pages)
        case "png" | "jpeg":
            kind = DocumentKind(signature)
            metadata = _check_image(path, kind)
            page_count = 1
        case "zip":
            kind = _check_ooxml(path)
        case "ole":
            raise UploadRejectedError(
                "Legacy Office formats (.doc/.xls/.ppt) and password-protected Office files "
                "are not supported. Save as DOCX/XLSX/PPTX without a password."
            )
        case _:
            raise UploadRejectedError(
                "Unsupported file type. Supported: PDF, PNG, JPEG, DOCX, XLSX, PPTX."
            )

    suffix = PurePosixPath(original_filename.lower()).suffix
    declared = _EXTENSIONS.get(suffix)
    if declared is not None and declared is not kind:
        raise UploadRejectedError(
            f"The file extension '{suffix}' does not match its content ({kind.value})."
        )

    return ValidatedUpload(
        path=path,
        sha256=sha256,
        size_bytes=size_bytes,
        kind=kind,
        page_count=page_count,
        metadata=metadata,
    )


def safe_display_name(filename: str | None) -> str:
    """Basename only, control characters stripped, length-capped. Metadata only — never a path."""
    name = PurePosixPath((filename or "").replace("\\", "/")).name
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    return name[:255] or "untitled"
