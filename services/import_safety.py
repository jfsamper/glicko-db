"""Shared safety limits for administrative data imports."""
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile


MAX_IMPORT_FILE_BYTES = 10 * 1024 * 1024
MAX_IMPORT_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_IMPORT_ROWS = 10_000


def save_upload_bounded(upload, destination, max_bytes=MAX_IMPORT_FILE_BYTES):
    """Save an uploaded stream while enforcing its compressed/file-size limit."""
    path = Path(destination)
    total_bytes = 0
    try:
        with path.open("wb") as target:
            while True:
                chunk = upload.stream.read(64 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise ValueError("import_file_too_large")
                target.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def validate_xlsx_archive(file_path):
    """Reject corrupt or excessively large decompressed XLSX archives."""
    try:
        with zipfile.ZipFile(file_path) as archive:
            if sum(entry.file_size for entry in archive.infolist()) > MAX_IMPORT_UNCOMPRESSED_BYTES:
                raise ValueError("import_uncompressed_too_large")
            if not any(name.startswith("xl/worksheets/") for name in archive.namelist()):
                raise ValueError("import_read_error")
    except ValueError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("import_read_error") from exc


def validate_xml_limits(file_path):
    """Bound OpenGotha participant/game counts before building a full XML tree."""
    rows = 0
    try:
        for _, element in ET.iterparse(file_path, events=("end",)):
            if element.tag.rsplit("}", 1)[-1] in {"Player", "Game"}:
                rows += 1
                if rows > MAX_IMPORT_ROWS:
                    raise ValueError("import_too_many_rows")
            element.clear()
    except ValueError:
        raise
    except (OSError, ET.ParseError) as exc:
        raise ValueError("import_read_error") from exc


def read_limited_rows(rows, max_rows=MAX_IMPORT_ROWS):
    """Materialize a bounded row iterator, rejecting the first over-limit row."""
    result = []
    for row in rows:
        if len(result) >= max_rows:
            raise ValueError("import_too_many_rows")
        result.append(row)
    return result
