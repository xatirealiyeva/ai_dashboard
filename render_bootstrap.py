"""Restore the ignored DuckDB snapshot from a Render Secret File, then start FastAPI."""

import base64
import gzip
import os
import sys
from pathlib import Path


def restore_database() -> None:
    destination = Path(os.getenv("DUCKDB_PATH", "/tmp/deposit_real.duckdb"))
    secret_file = Path(
        os.getenv("DUCKDB_SECRET_FILE", "/etc/secrets/deposit_real.duckdb.gz.b64")
    )

    if not secret_file.is_file():
        if destination.is_file():
            return
        raise RuntimeError(
            f"DuckDB snapshot is missing. Add {secret_file.name} as a Render Secret File."
        )

    try:
        compressed = base64.b64decode(secret_file.read_text(encoding="ascii"), validate=True)
        database = gzip.decompress(compressed)
    except (OSError, ValueError) as error:
        raise RuntimeError("Could not decode the DuckDB Render Secret File.") from error

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(database)


if __name__ == "__main__":
    restore_database()
    port = os.getenv("PORT", "8000")
    os.execvp(
        "uvicorn",
        ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", port],
    )
