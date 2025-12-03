"""Compatibility helpers for FastAPI/Pydantic on newer Python versions."""
import sys
from typing import ForwardRef

import pydantic.typing as pdt

_PATCHED = False


def patch_pydantic_forward_refs() -> None:
    """Fix ForwardRef evaluation on Python 3.12 for pydantic v1.x."""
    global _PATCHED
    if _PATCHED or sys.version_info < (3, 12):
        return

    try:
        def _evaluate_forwardref(type_: ForwardRef, globalns, localns):
            return type_._evaluate(globalns, localns, recursive_guard=set())

        pdt.evaluate_forwardref = _evaluate_forwardref  # type: ignore[attr-defined]
        _PATCHED = True
    except Exception:
        # If anything goes wrong, fail silently; FastAPI import will surface errors.
        pass
