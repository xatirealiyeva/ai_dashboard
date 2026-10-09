"""Load an external DuckDB snapshot into serverless ephemeral storage."""

import gzip
import base64
import os
import tempfile
from pathlib import Path
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_download_lock = Lock()


def decode_database_payload(payload: bytes, snapshot_url: str) -> bytes:
    if os.getenv("DUCKDB_URL_BASE64", "").strip().lower() in {"1", "true", "yes"}:
        payload = base64.b64decode(b"".join(payload.split()), validate=True)
    is_gzip = (
        os.getenv("DUCKDB_URL_GZIP", "").strip().lower() in {"1", "true", "yes"}
        or snapshot_url.split("?", 1)[0].lower().endswith(".gz")
        or payload[:2] == b"\x1f\x8b"
    )
    database_bytes = gzip.decompress(payload) if is_gzip else payload
    if not database_bytes.startswith(b"DUCK"):
        raise ValueError("Downloaded snapshot does not look like a DuckDB database")
    return database_bytes


def ensure_database(destination: str) -> str:
    database_path = Path(destination)
    if database_path.is_file():
        return str(database_path)

    snapshot_url = os.getenv("DUCKDB_URL", "").strip()
    if not snapshot_url:
        raise FileNotFoundError(
            f"DuckDB not found at {database_path}; configure DUCKDB_URL with a private snapshot URL"
        )

    with _download_lock:
        if database_path.is_file():
            return str(database_path)

        request = Request(snapshot_url)
        token = os.getenv("DUCKDB_URL_TOKEN", "").strip()
        if token:
            request.add_header("Authorization", f"Bearer {token}")

        database_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            with urlopen(request, timeout=45) as response:
                payload = response.read()
            database_bytes = decode_database_payload(payload, snapshot_url)
            with tempfile.NamedTemporaryFile(
                prefix=".deposit-db-",
                suffix=".tmp",
                dir=database_path.parent,
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(database_bytes)
            os.replace(temporary_path, database_path)
        except (HTTPError, URLError, OSError, EOFError, ValueError) as error:
            if temporary_path and temporary_path.exists():
                temporary_path.unlink()
            raise RuntimeError("Could not retrieve or validate the DuckDB snapshot") from error

    return str(database_path)