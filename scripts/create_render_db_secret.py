"""Create the base64-encoded gzip file uploaded to Render as a Secret File."""

import argparse
import base64
import gzip
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="deposit_real.duckdb")
    parser.add_argument("--output", default="deposit_real.duckdb.gz.b64")
    args = parser.parse_args()

    source = Path(args.input)
    if not source.is_file():
        raise SystemExit(f"Database file not found: {source}")

    encoded = base64.b64encode(gzip.compress(source.read_bytes(), compresslevel=9))
    Path(args.output).write_bytes(encoded)
    print(f"Created {args.output} ({len(encoded)} bytes). Keep this file private.")


if __name__ == "__main__":
    main()
