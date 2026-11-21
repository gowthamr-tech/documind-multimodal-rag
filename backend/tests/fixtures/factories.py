"""Build real test documents programmatically (no binary fixtures checked in)."""

import io
import struct
import zipfile
import zlib
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image
from pypdf import PdfWriter


def make_pdf(path: Path, pages: int = 2) -> Path:
    pdf = pdfium.PdfDocument.new()
    for _ in range(pages):
        pdf.new_page(612, 792)
    pdf.save(str(path))
    pdf.close()
    return path


def make_encrypted_pdf(path: Path) -> Path:
    plain = make_pdf(path.with_suffix(".plain.pdf"), pages=1)
    writer = PdfWriter(clone_from=str(plain))
    writer.encrypt(user_password="secret", owner_password="owner", algorithm="AES-256")
    with path.open("wb") as fh:
        writer.write(fh)
    return path


def make_png(path: Path, size: tuple[int, int] = (64, 48)) -> Path:
    Image.new("RGB", size, (200, 30, 30)).save(path, format="PNG")
    return path


def make_jpeg(path: Path) -> Path:
    Image.new("RGB", (32, 32), (10, 120, 200)).save(path, format="JPEG")
    return path


def make_png_with_declared_size(path: Path, width: int, height: int) -> Path:
    """A tiny file whose header *claims* huge dimensions (decompression-bomb shape)."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" * 16)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    )
    return path


def _ooxml(path: Path, main_part: str, extra: dict[str, bytes] | None = None) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        zf.writestr(main_part, '<?xml version="1.0"?><root/>')
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return path


def make_docx(path: Path) -> Path:
    return _ooxml(path, "word/document.xml")


def make_xlsx(path: Path) -> Path:
    return _ooxml(path, "xl/workbook.xml")


def make_pptx(path: Path) -> Path:
    return _ooxml(path, "ppt/presentation.xml")


def make_zip_bomb_docx(path: Path) -> Path:
    # 20 MB of zeros deflates to ~20 KB: ratio ~1000x, far beyond the limit.
    return _ooxml(path, "word/document.xml", {"word/media/pad.bin": b"\x00" * (20 * 1024 * 1024)})


def make_traversal_docx(path: Path) -> Path:
    return _ooxml(path, "word/document.xml", {"../../evil.sh": b"echo pwned"})


def make_plain_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("readme.txt", "hello")
    return path


def make_ole(path: Path) -> Path:
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512)
    return path


def bytes_of(path: Path) -> io.BytesIO:
    return io.BytesIO(path.read_bytes())
