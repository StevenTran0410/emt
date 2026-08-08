"""Lazy tree-sitter Language factory functions, one per supported language.

Each factory imports its tree-sitter grammar package lazily (only when the
language is actually used) and wraps it in a tree_sitter.Language instance.
"""
from __future__ import annotations

from typing import Any


def _make_python_lang() -> Any:
    import tree_sitter_python as tspython
    from tree_sitter import Language
    return Language(tspython.language())


def _make_typescript_lang() -> Any:
    import tree_sitter_typescript as tsts
    from tree_sitter import Language
    return Language(tsts.language_typescript())


def _make_javascript_lang() -> Any:
    import tree_sitter_javascript as tsjs
    from tree_sitter import Language
    return Language(tsjs.language())


def _make_cpp_lang() -> Any:
    import tree_sitter_cpp as tscpp
    from tree_sitter import Language
    return Language(tscpp.language())


def _make_go_lang() -> Any:
    import tree_sitter_go as tsgo
    from tree_sitter import Language
    return Language(tsgo.language())


def _make_java_lang() -> Any:
    import tree_sitter_java as tsjava
    from tree_sitter import Language
    return Language(tsjava.language())


def _make_c_lang() -> Any:
    import tree_sitter_c as tsc
    from tree_sitter import Language
    return Language(tsc.language())


def _make_rust_lang() -> Any:
    import tree_sitter_rust as tsrust
    from tree_sitter import Language
    return Language(tsrust.language())


def _make_ruby_lang() -> Any:
    import tree_sitter_ruby as m
    from tree_sitter import Language
    return Language(m.language())


def _make_php_lang() -> Any:
    import tree_sitter_php as m
    from tree_sitter import Language
    return Language(m.language_php())


def _make_csharp_lang() -> Any:
    import tree_sitter_c_sharp as m
    from tree_sitter import Language
    return Language(m.language())


def _make_kotlin_lang() -> Any:
    import tree_sitter_kotlin as m
    from tree_sitter import Language
    return Language(m.language())


def _make_scala_lang() -> Any:
    import tree_sitter_scala as m
    from tree_sitter import Language
    return Language(m.language())


def _make_bash_lang() -> Any:
    import tree_sitter_bash as m
    from tree_sitter import Language
    return Language(m.language())


def _make_lua_lang() -> Any:
    import tree_sitter_lua as m
    from tree_sitter import Language
    return Language(m.language())


def _make_zig_lang() -> Any:
    import tree_sitter_zig as m
    from tree_sitter import Language
    return Language(m.language())


def _make_haskell_lang() -> Any:
    import tree_sitter_haskell as m
    from tree_sitter import Language
    return Language(m.language())


def _make_elixir_lang() -> Any:
    import tree_sitter_elixir as m
    from tree_sitter import Language
    return Language(m.language())


def _make_ocaml_lang() -> Any:
    import tree_sitter_ocaml as m
    from tree_sitter import Language
    return Language(m.language_ocaml())


def _make_julia_lang() -> Any:
    import tree_sitter_julia as m
    from tree_sitter import Language
    return Language(m.language())


def _make_yaml_lang() -> Any:
    import tree_sitter_yaml as m
    from tree_sitter import Language
    return Language(m.language())


def _make_toml_lang() -> Any:
    import tree_sitter_toml as m
    from tree_sitter import Language
    return Language(m.language())


def _make_html_lang() -> Any:
    import tree_sitter_html as m
    from tree_sitter import Language
    return Language(m.language())


def _make_css_lang() -> Any:
    import tree_sitter_css as m
    from tree_sitter import Language
    return Language(m.language())


def _make_json_lang() -> Any:
    import tree_sitter_json as m
    from tree_sitter import Language
    return Language(m.language())


def _make_markdown_lang() -> Any:
    import tree_sitter_markdown as m
    from tree_sitter import Language
    return Language(m.language())


def _make_groovy_lang() -> Any:
    import tree_sitter_groovy as m
    from tree_sitter import Language
    return Language(m.language())


def _make_cmake_lang() -> Any:
    import tree_sitter_cmake as m
    from tree_sitter import Language
    return Language(m.language())


def _make_svelte_lang() -> Any:
    import tree_sitter_svelte as m
    from tree_sitter import Language
    return Language(m.language())


def _make_sql_lang() -> Any:
    import tree_sitter_sql as m
    from tree_sitter import Language
    return Language(m.language())
