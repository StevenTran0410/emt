"""Tree-sitter parser cache keyed by language."""
from __future__ import annotations

import logging
from typing import Any

from ._lang_configs import LANGUAGE_CONFIGS

logger = logging.getLogger(__name__)

_parser_cache: dict[str, Any] = {}


def _get_parser(language: str) -> Any | None:
    """Return a cached tree_sitter.Parser for the given language, or None."""
    if language in _parser_cache:
        return _parser_cache[language]
    cfg = LANGUAGE_CONFIGS.get(language)
    if cfg is None:
        return None
    try:
        from tree_sitter import Parser
        lang = cfg.get_language()
        parser = Parser(lang)
        _parser_cache[language] = parser
        return parser
    except Exception as exc:
        logger.warning("[chunker_ast] failed to load parser for %s: %s", language, exc)
        _parser_cache[language] = None
        return None
