"""Lazy singleton loader for the optional native (C++) graph acceleration module."""
from __future__ import annotations

import importlib

_NATIVE = None


def _get_native():
    global _NATIVE
    if _NATIVE is None:
        try:
            _NATIVE = importlib.import_module("domain.structural_graph._native_graph")
        except Exception:
            pass
    return _NATIVE
