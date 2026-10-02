#!/usr/bin/env python3
"""Create a private local .env without printing keys or replacing existing files."""
import argparse
import os
from pathlib import Path
import secrets

ROOT = Path(__file__).resolve().parents[1]


def create_config(destination: Path, template: Path = ROOT / ".env.example") -> bool:
    contents = template.read_text(encoding="utf-8")
    marker = "MIROFISH_ACCESS_KEY=\n"
    if marker not in contents:
        raise ValueError("Configuration template lacks an empty access-key field")
    contents = contents.replace(marker, "MIROFISH_ACCESS_KEY=" + secrets.token_urlsafe(48) + "\n", 1)
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(contents)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    if create_config(args.output):
        print("Created private .env with a random workspace access key. Add ZEP_API_KEY and review the LLM settings before starting.")
    else:
        print("Configuration already exists; kept it unchanged. Check that MIROFISH_ACCESS_KEY has at least 32 characters.")


if __name__ == "__main__":
    main()
