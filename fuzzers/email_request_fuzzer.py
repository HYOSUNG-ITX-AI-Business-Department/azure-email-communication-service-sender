"""Atheris fuzz harness for EmailRequest schema validation.

This is intentionally defensive:
- Invalid JSON / schema inputs are expected and should not crash.
- Unexpected exceptions should be surfaced as crashes.
"""

from __future__ import annotations

import json
import sys

import atheris
from pydantic import ValidationError

with atheris.instrument_imports():
    from app.schemas.email import EmailRequest

# Maximum input size to avoid excessive CPU/memory usage on pathological inputs.
MAX_INPUT_SIZE = 200_000


def _should_skip_input(data: bytes) -> bool:
    # Avoid excessive CPU/memory usage on pathological inputs.
    return len(data) > MAX_INPUT_SIZE


def TestOneInput(data: bytes) -> None:
    if _should_skip_input(data):
        return

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return

    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return

    if not isinstance(obj, dict):
        return

    try:
        EmailRequest.model_validate(obj)
    except ValidationError:
        # Invalid inputs are expected.
        return


def main() -> None:
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
