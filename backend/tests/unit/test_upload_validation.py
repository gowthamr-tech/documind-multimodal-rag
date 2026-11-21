import hashlib
import io
from collections.abc import Callable
from pathlib import Path

import pytest

from app.ingestion.validation import (
    DocumentKind,
    UploadRejectedError,
    UploadTooLargeError,
    safe_display_name,
    spool_to_disk,
    validate_file,
)
from tests.fixtures import factories as f


def _validate(path: Path, filename: str | None = None, max_pages: int = 100):  # type: ignore[no-untyped-def]
    data = path.read_bytes()
    return validate_file(
        path,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        original_filename=filename or path.name,
        max_pages=max_pages,
    )


@pytest.mark.parametrize(
    ("factory", "name", "kind"),
    [
        (f.make_pdf, "a.pdf", DocumentKind.PDF),
        (f.make_png, "a.png", DocumentKind.PNG),
        (f.make_jpeg, "a.jpg", DocumentKind.JPEG),
        (f.make_docx, "a.docx", DocumentKind.DOCX),
        (f.make_xlsx, "a.xlsx", DocumentKind.XLSX),
        (f.make_pptx, "a.pptx", DocumentKind.PPTX),
    ],
)
def test_supported_formats_detected_from_content(
    tmp_path: Path, factory: Callable[[Path], Path], name: str, kind: DocumentKind
) -> None:
    result = _validate(factory(tmp_path / name))
    assert result.kind is kind


def test_pdf_page_count_and_limit(tmp_path: Path) -> None:
    pdf = f.make_pdf(tmp_path / "three.pdf", pages=3)
    assert _validate(pdf).page_count == 3
    with pytest.raises(UploadRejectedError, match="limit is 2"):
        _validate(pdf, max_pages=2)


def test_type_comes_from_content_not_extension(tmp_path: Path) -> None:
    # Unknown/absent extension: content decides.
    assert _validate(f.make_pdf(tmp_path / "x.bin"), filename="report").kind is DocumentKind.PDF


def test_contradicting_extension_rejected(tmp_path: Path) -> None:
    with pytest.raises(UploadRejectedError, match="does not match"):
        _validate(f.make_docx(tmp_path / "x.docx"), filename="invoice.pdf")


def test_encrypted_pdf_rejected(tmp_path: Path) -> None:
    with pytest.raises(UploadRejectedError, match="password"):
        _validate(f.make_encrypted_pdf(tmp_path / "locked.pdf"))


def test_corrupt_pdf_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.7\nthis is not a pdf body")
    with pytest.raises(UploadRejectedError, match="corrupt"):
        _validate(bad)


def test_zip_bomb_rejected(tmp_path: Path) -> None:
    with pytest.raises(UploadRejectedError, match="compression bomb"):
        _validate(f.make_zip_bomb_docx(tmp_path / "bomb.docx"))


def test_path_traversal_entry_rejected(tmp_path: Path) -> None:
    with pytest.raises(UploadRejectedError, match="unsafe paths"):
        _validate(f.make_traversal_docx(tmp_path / "evil.docx"))


def test_plain_zip_rejected(tmp_path: Path) -> None:
    with pytest.raises(UploadRejectedError, match="ZIP archives"):
        _validate(f.make_plain_zip(tmp_path / "a.zip"))


def test_legacy_or_encrypted_office_rejected(tmp_path: Path) -> None:
    with pytest.raises(UploadRejectedError, match="Legacy Office"):
        _validate(f.make_ole(tmp_path / "old.doc"))


def test_image_with_huge_declared_dimensions_rejected(tmp_path: Path) -> None:
    bomb = f.make_png_with_declared_size(tmp_path / "bomb.png", 50_000, 50_000)
    assert bomb.stat().st_size < 1024
    with pytest.raises(UploadRejectedError):
        _validate(bomb)


def test_unknown_type_rejected(tmp_path: Path) -> None:
    exe = tmp_path / "a.exe"
    exe.write_bytes(b"MZ\x90\x00" + b"\x00" * 100)
    with pytest.raises(UploadRejectedError, match="Unsupported file type"):
        _validate(exe)


def test_spool_enforces_size_while_streaming(tmp_path: Path) -> None:
    with pytest.raises(UploadTooLargeError):
        spool_to_disk(io.BytesIO(b"x" * 2048), max_bytes=1024, tmp_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []  # partial temp file cleaned up


def test_spool_hashes_and_rejects_empty(tmp_path: Path) -> None:
    path, digest, size = spool_to_disk(io.BytesIO(b"hello"), max_bytes=1024, tmp_dir=tmp_path)
    assert size == 5
    assert digest == hashlib.sha256(b"hello").hexdigest()
    path.unlink()
    with pytest.raises(UploadRejectedError, match="empty"):
        spool_to_disk(io.BytesIO(b""), max_bytes=1024, tmp_dir=tmp_path)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\x\\report.pdf", "report.pdf"),
        ("evil\x00name\n.pdf", "evilname.pdf"),
        ("", "untitled"),
        (None, "untitled"),
    ],
)
def test_safe_display_name(raw: str | None, expected: str) -> None:
    assert safe_display_name(raw) == expected
