"""Tree-sitter walker registry and per-language extraction dispatch."""
from shared.logger import logger

from .._loaders import _get_ts_lang, _Symbol, _ts_parse
from .._walkers_config import _walk_cmake, _walk_json, _walk_sql, _walk_toml, _walk_yaml
from .._walkers_functional import _walk_elixir, _walk_haskell, _walk_julia, _walk_ocaml
from .._walkers_js_ts import _walk_js_ts
from .._walkers_jvm import _walk_groovy, _walk_java, _walk_kotlin, _walk_scala
from .._walkers_markup import _walk_css, _walk_html, _walk_markdown, _walk_svelte
from .._walkers_python import _walk_python_ts
from .._walkers_scripting import _walk_csharp, _walk_lua, _walk_php, _walk_ruby
from .._walkers_systems import _walk_bash, _walk_c_cpp, _walk_go, _walk_rust, _walk_zig

_WALKERS: dict[str, object] = {
    # Core
    "python":     lambda root: _walk_python_ts(root),
    "javascript": lambda root: _walk_js_ts(root, is_ts=False),
    "typescript": lambda root: _walk_js_ts(root, is_ts=True),
    "go":         _walk_go,
    "rust":       _walk_rust,
    "c":          lambda root: _walk_c_cpp(root, is_cpp=False),
    "cpp":        lambda root: _walk_c_cpp(root, is_cpp=True),
    "c++":        lambda root: _walk_c_cpp(root, is_cpp=True),
    "zig":        _walk_zig,
    "bash":       _walk_bash,
    "sh":         _walk_bash,
    # JVM
    "java":    _walk_java,
    "kotlin":  _walk_kotlin,
    "scala":   _walk_scala,
    "groovy":  _walk_groovy,
    # Scripting
    "ruby":   _walk_ruby,
    "php":    _walk_php,
    "lua":    _walk_lua,
    "csharp": _walk_csharp,
    # Functional
    "haskell": _walk_haskell,
    "ocaml":   _walk_ocaml,
    "elixir":  _walk_elixir,
    "julia":   _walk_julia,
    # Config / data
    "yaml":     _walk_yaml,
    "toml":     _walk_toml,
    "json":     _walk_json,
    "cmake":    _walk_cmake,
    "sql":      _walk_sql,
    # Markup / styling
    "html":     _walk_html,
    "css":      _walk_css,
    "markdown": _walk_markdown,
    "svelte":   _walk_svelte,
}


def _has_treesitter_support(language: str | None) -> bool:
    """True if a tree-sitter walker exists for this language.

    Languages without one (e.g. COBOL/JCL — parsed structurally by ANTLR in the
    structural graph) are handled by the lexical walker; a tree-sitter miss on them is
    expected, not a parse failure.
    """
    return _WALKERS.get((language or "").lower()) is not None


def _extract_symbols_treesitter(content: str, language: str) -> list[_Symbol]:
    low = (language or "").lower()
    walker = _WALKERS.get(low)
    if walker is None:
        return []
    lang_obj = _get_ts_lang(low)
    if lang_obj is None:
        return []
    root = _ts_parse(content, lang_obj)
    if root is None:
        return []
    try:
        return walker(root)  # type: ignore[operator,misc]
    except Exception as exc:
        logger.warning("tree-sitter extraction failed for %s: %s", language, exc)
        return []
