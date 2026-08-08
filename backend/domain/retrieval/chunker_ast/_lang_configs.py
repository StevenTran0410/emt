"""Per-language chunking configuration: semantic/import tree-sitter node types."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ._lang_factories import (
    _make_bash_lang,
    _make_c_lang,
    _make_cmake_lang,
    _make_cpp_lang,
    _make_csharp_lang,
    _make_css_lang,
    _make_elixir_lang,
    _make_go_lang,
    _make_groovy_lang,
    _make_haskell_lang,
    _make_html_lang,
    _make_java_lang,
    _make_javascript_lang,
    _make_json_lang,
    _make_julia_lang,
    _make_kotlin_lang,
    _make_lua_lang,
    _make_markdown_lang,
    _make_ocaml_lang,
    _make_php_lang,
    _make_python_lang,
    _make_ruby_lang,
    _make_rust_lang,
    _make_scala_lang,
    _make_sql_lang,
    _make_svelte_lang,
    _make_toml_lang,
    _make_typescript_lang,
    _make_yaml_lang,
    _make_zig_lang,
)


@dataclass(frozen=True)
class LanguageConfig:
    get_language: Callable[[], Any]       # lazy factory returning tree_sitter.Language
    semantic_node_types: frozenset[str]
    import_node_types: frozenset[str]


LANGUAGE_CONFIGS: dict[str, LanguageConfig] = {
    "python": LanguageConfig(
        get_language=_make_python_lang,
        semantic_node_types=frozenset({
            "function_definition",        # covers both sync and async in tree-sitter-python >=0.25
            "async_function_definition",  # kept for tree-sitter-python <0.25 compatibility
            "class_definition",
            "decorated_definition",
        }),
        import_node_types=frozenset({
            "import_statement",
            "import_from_statement",
        }),
    ),
    "typescript": LanguageConfig(
        get_language=_make_typescript_lang,
        semantic_node_types=frozenset({
            "function_declaration",
            "generator_function_declaration",
            "class_declaration",
            "method_definition",
            "export_statement",
            "lexical_declaration",
            "variable_declaration",
            "interface_declaration",      # TS interfaces are first-class semantic units
            "type_alias_declaration",     # type Foo = ... declarations
        }),
        import_node_types=frozenset({
            "import_statement",
        }),
    ),
    "javascript": LanguageConfig(
        get_language=_make_javascript_lang,
        semantic_node_types=frozenset({
            "function_declaration",
            "generator_function_declaration",
            "class_declaration",
            "method_definition",
            "export_statement",
            "lexical_declaration",
            "variable_declaration",
        }),
        import_node_types=frozenset({
            "import_statement",
        }),
    ),
    "cpp": LanguageConfig(
        get_language=_make_cpp_lang,
        semantic_node_types=frozenset({
            "function_definition",
            "class_specifier",
            "struct_specifier",
            "namespace_definition",
            "template_declaration",
        }),
        import_node_types=frozenset({
            "preproc_include",
        }),
    ),
    "go": LanguageConfig(
        get_language=_make_go_lang,
        semantic_node_types=frozenset({
            "function_declaration",
            "method_declaration",
            "type_declaration",
            "var_declaration",
            "const_declaration",
        }),
        import_node_types=frozenset({
            "import_declaration",
        }),
    ),
    "java": LanguageConfig(
        get_language=_make_java_lang,
        semantic_node_types=frozenset({
            "class_declaration",
            "interface_declaration",
            "enum_declaration",
            "annotation_type_declaration",
            "method_declaration",
            "constructor_declaration",
            "field_declaration",
        }),
        import_node_types=frozenset({
            "import_declaration",
        }),
    ),
    "c": LanguageConfig(
        get_language=_make_c_lang,
        semantic_node_types=frozenset({
            "function_definition",
            "struct_specifier",
            "enum_specifier",
            "union_specifier",
            "type_definition",
        }),
        import_node_types=frozenset({
            "preproc_include",
            "preproc_def",           # #define CONSTANT
            "preproc_function_def",  # #define MACRO(x) ...
        }),
    ),
    "rust": LanguageConfig(
        get_language=_make_rust_lang,
        semantic_node_types=frozenset({
            "function_item",
            "impl_item",
            "struct_item",
            "enum_item",
            "trait_item",
            "mod_item",
            "type_item",
        }),
        import_node_types=frozenset({
            "use_declaration",
        }),
    ),
    "ruby": LanguageConfig(
        get_language=_make_ruby_lang,
        semantic_node_types=frozenset({
            "method",
            "singleton_method",
            "class",
            "module",
        }),
        import_node_types=frozenset({
            "call",  # require / require_relative
        }),
    ),
    "php": LanguageConfig(
        get_language=_make_php_lang,
        semantic_node_types=frozenset({
            "function_definition",
            "class_declaration",
            "method_declaration",
            "interface_declaration",
            "trait_declaration",
        }),
        import_node_types=frozenset({
            "require_expression",
            "include_expression",
        }),
    ),
    "csharp": LanguageConfig(
        get_language=_make_csharp_lang,
        semantic_node_types=frozenset({
            "class_declaration",
            "interface_declaration",
            "struct_declaration",
            "record_declaration",
            "enum_declaration",
            "method_declaration",
            "constructor_declaration",
            "namespace_declaration",
        }),
        import_node_types=frozenset({
            "using_directive",
        }),
    ),
    "kotlin": LanguageConfig(
        get_language=_make_kotlin_lang,
        semantic_node_types=frozenset({
            "class_declaration",
            "object_declaration",
            "function_declaration",
            "secondary_constructor",
        }),
        import_node_types=frozenset({
            "import_header",
        }),
    ),
    "scala": LanguageConfig(
        get_language=_make_scala_lang,
        semantic_node_types=frozenset({
            "class_definition",
            "object_definition",
            "trait_definition",
            "function_definition",
        }),
        import_node_types=frozenset({
            "import_declaration",
        }),
    ),
    "bash": LanguageConfig(
        get_language=_make_bash_lang,
        semantic_node_types=frozenset({
            "function_definition",
        }),
        import_node_types=frozenset({
            "command",  # source / . commands
        }),
    ),
    "sh": LanguageConfig(
        get_language=_make_bash_lang,
        semantic_node_types=frozenset({
            "function_definition",
        }),
        import_node_types=frozenset({
            "command",
        }),
    ),
    "lua": LanguageConfig(
        get_language=_make_lua_lang,
        semantic_node_types=frozenset({
            "function_declaration",
            "local_function",
        }),
        import_node_types=frozenset({
            "function_call",  # require(...)
        }),
    ),
    "zig": LanguageConfig(
        get_language=_make_zig_lang,
        semantic_node_types=frozenset({
            "function_declaration",
            "variable_declaration",
        }),
        import_node_types=frozenset({
            "builtin_call",  # @import
        }),
    ),
    "haskell": LanguageConfig(
        get_language=_make_haskell_lang,
        semantic_node_types=frozenset({
            "function",
            "data_declaration",
            "type_synonym",
            "class_declaration",
            "instance_declaration",
            "newtype_declaration",
        }),
        import_node_types=frozenset({
            "import",
        }),
    ),
    "elixir": LanguageConfig(
        get_language=_make_elixir_lang,
        semantic_node_types=frozenset({
            "call",  # defmodule, def, defp, defmacro all parse as call
        }),
        import_node_types=frozenset({
            "alias",
            "import",
            "require",
            "use",
        }),
    ),
    "ocaml": LanguageConfig(
        get_language=_make_ocaml_lang,
        semantic_node_types=frozenset({
            "value_definition",
            "type_definition",
            "module_definition",
            "exception_definition",
            "class_definition",
        }),
        import_node_types=frozenset({
            "open_statement",
        }),
    ),
    "julia": LanguageConfig(
        get_language=_make_julia_lang,
        semantic_node_types=frozenset({
            "function_definition",
            "short_function_definition",
            "macro_definition",
            "struct_definition",
            "module_definition",
            "abstract_definition",
        }),
        import_node_types=frozenset({
            "import_statement",
            "using_statement",
        }),
    ),
    "yaml": LanguageConfig(
        get_language=_make_yaml_lang,
        semantic_node_types=frozenset({
            "block_mapping_pair",
            "block_sequence",
        }),
        import_node_types=frozenset(),
    ),
    "toml": LanguageConfig(
        get_language=_make_toml_lang,
        semantic_node_types=frozenset({
            "table",
            "array_table",
        }),
        import_node_types=frozenset(),
    ),
    "html": LanguageConfig(
        get_language=_make_html_lang,
        semantic_node_types=frozenset({
            "element",
            "script_element",
            "style_element",
        }),
        import_node_types=frozenset(),
    ),
    "css": LanguageConfig(
        get_language=_make_css_lang,
        semantic_node_types=frozenset({
            "rule_set",
            "media_statement",
            "keyframes_statement",
            "at_rule",
        }),
        import_node_types=frozenset({
            "import_statement",
        }),
    ),
    "json": LanguageConfig(
        get_language=_make_json_lang,
        semantic_node_types=frozenset({
            "object",
            "array",
        }),
        import_node_types=frozenset(),
    ),
    "markdown": LanguageConfig(
        get_language=_make_markdown_lang,
        semantic_node_types=frozenset({
            "section",
            "atx_heading",
            "setext_heading",
        }),
        import_node_types=frozenset(),
    ),
    "groovy": LanguageConfig(
        get_language=_make_groovy_lang,
        semantic_node_types=frozenset({
            "class_declaration",
            "method_declaration",
            "function_definition",
            "closure",
        }),
        import_node_types=frozenset({
            "import_declaration",
        }),
    ),
    "cmake": LanguageConfig(
        get_language=_make_cmake_lang,
        semantic_node_types=frozenset({
            "function_def",
            "macro_def",
        }),
        import_node_types=frozenset({
            "include_command",
        }),
    ),
    "svelte": LanguageConfig(
        get_language=_make_svelte_lang,
        semantic_node_types=frozenset({
            "script_element",
            "style_element",
            "element",
        }),
        import_node_types=frozenset(),
    ),
    "sql": LanguageConfig(
        get_language=_make_sql_lang,
        semantic_node_types=frozenset({
            "statement",
        }),
        import_node_types=frozenset(),
    ),
}
