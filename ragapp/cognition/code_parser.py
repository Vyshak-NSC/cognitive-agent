# ragapp/cognition/code_parser.py

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import hashlib
import re


# ---------------------------------------------------------------------------
# Normalized representation
# ---------------------------------------------------------------------------

@dataclass
class CodeSymbol:
    id: str
    kind: str
    name: str
    file: str
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    parent_id: str | None = None
    signature: str = ""
    children: list[str] = field(default_factory=list)


@dataclass
class CodeReference:
    source_symbol: str | None
    target_name: str
    kind: str
    file: str
    start_line: int
    end_line: int
    imported_names: list[str] = field(default_factory=list)


@dataclass
class ParsedFile:
    path: str
    language: str
    file_id: str
    symbols: list[CodeSymbol] = field(default_factory=list)
    references: list[CodeReference] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

EXTENSION_LANGUAGE = {
    ".py": "python",
    ".pyw": "python",

    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",

    ".ts": "typescript",
    ".tsx": "typescript",

    ".java": "java",

    ".c": "c",
    ".h": "c",

    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",

    ".cs": "c_sharp",

    ".go": "go",

    ".rs": "rust",

    ".rb": "ruby",

    ".php": "php",

    ".swift": "swift",

    ".kt": "kotlin",
    ".kts": "kotlin",

    ".scala": "scala",

    ".lua": "lua",

    ".dart": "dart",

    ".ex": "elixir",
    ".exs": "elixir",

    ".erl": "erlang",
    ".hrl": "erlang",

    ".hs": "haskell",

    ".sh": "bash",

    ".sql": "sql",

    ".html": "html",

    ".css": "css",

    ".json": "json",

    ".yaml": "yaml",
    ".yml": "yaml",

    ".xml": "xml",
}


def detect_language(path: str | Path) -> str | None:
    """Return the Tree-sitter language for a recognized source extension.

    Returns None for files that are not recognized as source code.
    """

    return EXTENSION_LANGUAGE.get(
        Path(path).suffix.lower()
    )


# ---------------------------------------------------------------------------
# Tree-sitter loading
# ---------------------------------------------------------------------------

class TreeSitterParser:
    """Small compatibility wrapper around Tree-sitter.

    Grammars are loaded lazily and cached per language.
    """

    def __init__(self):
        self._parsers: dict[str, Any] = {}
        self._languages: dict[str, Any] = {}

        try:
            from tree_sitter import Parser
            self._Parser = Parser
        except ImportError as exc:
            raise RuntimeError(
                "tree-sitter is required. Install tree-sitter>=0.21.0,<0.22.0"
            ) from exc

        try:
            from tree_sitter_languages import get_language
            self._get_language = get_language
        except ImportError as exc:
            raise RuntimeError(
                "tree-sitter-languages is required. Install tree-sitter-languages>=1.10.2"
            ) from exc

    def parser_for(self, language: str):
        """Return a cached parser for a language."""

        if language in self._parsers:
            return self._parsers[language]

        try:
            grammar = self._get_language(language)
        except Exception as exc:
            raise ValueError(
                f"Tree-sitter grammar load failed for language '{language}': "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        parser = self._Parser()

        try:
            parser.language = grammar
        except Exception:
            parser.set_language(grammar)

        self._languages[language] = grammar
        self._parsers[language] = parser

        return parser

    def parse(
        self,
        source: bytes,
        language: str,
    ):
        parser = self.parser_for(language)

        return parser.parse(source)


# ---------------------------------------------------------------------------
# Generic syntax extraction
# ---------------------------------------------------------------------------

class CodeParser:
    """Parse source code into deterministic normalized cognition facts.

    This class does NOT use an LLM.

    It extracts facts directly observable from source:

    - functions
    - methods
    - classes
    - interfaces
    - structs
    - enums
    - traits
    - modules
    - namespaces
    - type declarations
    - records
    - properties
    - fields
    - variables
    - constants
    - imports/includes
    - function/method calls
    - parent/child nesting
    - source locations
    """

    # ------------------------------------------------------------------
    # Symbol node mapping
    # ------------------------------------------------------------------

    SYMBOL_NODES = {
        "function_definition": "function",
        "function_declaration": "function",

        "method_definition": "method",
        "method_declaration": "method",

        "constructor_declaration": "constructor",

        "class_definition": "class",
        "class_declaration": "class",

        "interface_declaration": "interface",

        "struct_declaration": "struct",
        "struct_specifier": "struct",

        "enum_declaration": "enum",
        "enum_specifier": "enum",

        "trait_item": "trait",
        "trait_declaration": "trait",

        "module": "module",
        "module_declaration": "module",

        "namespace_definition": "namespace",
        "namespace_declaration": "namespace",

        "type_definition": "type",
        "type_declaration": "type",

        "record_declaration": "record",

        "property_declaration": "property",
        "field_declaration": "field",

        "variable_declaration": "variable",
        "lexical_declaration": "variable",

        "constant_declaration": "constant",
    }

    # ------------------------------------------------------------------
    # Import/reference nodes
    # ------------------------------------------------------------------

    IMPORT_NODES = {
        "import_statement",
        "import_from_statement",  # Python: "from X import Y" (was missing -
                                   # only plain "import X" was ever detected)
        "import_declaration",
        "import_specification",
        "preproc_include",
        "using_directive",
        "using_declaration",
        "package_clause",
        "require",
    }

    CALL_NODES = {
        "call",
        "call_expression",
        "method_invocation",
        "function_call",
        "macro_invocation",
    }

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self):
        self.ts = TreeSitterParser()

    # ------------------------------------------------------------------
    # Public parsing
    # ------------------------------------------------------------------

    def parse_file(
        self,
        path: str | Path,
        source: str | bytes,
        language: str | None = None,
    ) -> ParsedFile:
        """Parse one source file.

        Parsing is deterministic. No LLM or network access is used.
        """

        path = Path(path).as_posix()

        if language is None:
            language = detect_language(path)

        if not language:
            raise ValueError(
                f"Cannot determine language for {path}"
            )

        if isinstance(source, str):
            source_bytes = source.encode("utf-8")
        else:
            source_bytes = source

        file_id = f"file:{path}"

        result = ParsedFile(
            path=path,
            language=language,
            file_id=file_id,
        )

        try:
            tree = self.ts.parse(
                source_bytes,
                language,
            )

        except Exception as exc:
            # IMPORTANT: this used to swallow the error into result.errors
            # and return a "successful" ParsedFile with zero symbols/
            # imports/references. Callers (code_compiler.py) only route a
            # file into its `failed` list when parse_code() raises, so a
            # swallowed grammar-load failure was previously indistinguishable
            # from "this file genuinely has 0 symbols" and got persisted
            # into cognition as if it were correct. Re-raise so it is
            # counted as a real failure instead.
            raise RuntimeError(
                f"Failed to parse {path} as {language}: {exc}"
            ) from exc

        if tree.root_node.has_error:
            result.errors.append(
                "Tree-sitter detected syntax errors"
            )

        self._walk(
            tree.root_node,
            source_bytes,
            result,
            parent_id=None,
        )

        return result

    # ------------------------------------------------------------------
    # Tree traversal
    # ------------------------------------------------------------------

    def _walk(
        self,
        node,
        source: bytes,
        result: ParsedFile,
        parent_id: str | None,
    ):
        """Walk the Tree-sitter AST.

        If a node represents a symbol, that symbol becomes the parent
        context for its children.

        Imports and calls are recorded as references.
        """

        node_type = node.type

        # --------------------------------------------------------------
        # Symbol
        #
        # IMPORTANT: node.parent is None only for the tree's root node
        # (the whole file). Several grammars name that root node the same
        # as a real declaration type - tree-sitter-python calls it
        # "module", which collided with SYMBOL_NODES["module"] and caused
        # every file to get a phantom "module" entity whose name came from
        # a random nested identifier (usually the first function's name).
        # That bogus entity also became the parent of every real top-level
        # symbol, corrupting their qualified names (e.g. a sibling
        # function `multiply` showed up as "add.multiply" as if nested
        # inside `add`). Excluding the true root node fixes both.
        # --------------------------------------------------------------

        if (
            node.parent is not None
            and node_type in self.SYMBOL_NODES
        ):
            symbol = self._make_symbol(
                node=node,
                source=source,
                result=result,
                parent_id=parent_id,
            )

            result.symbols.append(symbol)

            if parent_id:
                parent = next(
                    (
                        item
                        for item in result.symbols
                        if item.id == parent_id
                    ),
                    None,
                )

                if parent:
                    parent.children.append(
                        symbol.id
                    )

            current_parent = symbol.id

        else:
            current_parent = parent_id

        # --------------------------------------------------------------
        # Import
        # --------------------------------------------------------------

        if node_type in self.IMPORT_NODES:
            if result.language == "python":
                target_name, imported_names = (
                    self._extract_import_reference(
                        node,
                        source,
                    )
                )
            else:
                target_name = self._node_text(
                    node,
                    source,
                ).strip()

                imported_names = []

            if target_name:
                result.imports.append(
                    target_name
                )

                result.references.append(
                    CodeReference(
                        source_symbol=current_parent,
                        target_name=target_name,
                        kind="import",
                        file=result.path,
                        start_line=(
                            node.start_point[0] + 1
                        ),
                        end_line=(
                            node.end_point[0] + 1
                        ),
                        imported_names=imported_names,
                    )
                )

        # --------------------------------------------------------------
        # Call
        # --------------------------------------------------------------

        if node_type in self.CALL_NODES:
            target = self._extract_call_target(
                node,
                source,
            )

            if target:
                result.references.append(
                    CodeReference(
                        source_symbol=current_parent,
                        target_name=target,
                        kind="call",
                        file=result.path,
                        start_line=(
                            node.start_point[0] + 1
                        ),
                        end_line=(
                            node.end_point[0] + 1
                        ),
                    )
                )

        # --------------------------------------------------------------
        # Children
        # --------------------------------------------------------------

        for child in node.children:
            self._walk(
                child,
                source,
                result,
                current_parent,
            )

    # ------------------------------------------------------------------
    # Symbol construction
    # ------------------------------------------------------------------

    def _make_symbol(
        self,
        node,
        source: bytes,
        result: ParsedFile,
        parent_id: str | None,
    ) -> CodeSymbol:
        """Convert a Tree-sitter declaration into CodeSymbol.

        IMPORTANT:

        The symbol ID intentionally does NOT contain the source line.

        Therefore moving a function from line 10 to line 100 does not
        create a completely new cognition entity.

        The ID is based on:

            file + symbol kind + symbol name

        This gives stable identity across source movement.
        """

        kind = self.SYMBOL_NODES[
            node.type
        ]

        name = self._extract_name(
            node,
            source,
        )

        if not name:
            name = (
                f"<anonymous:"
                f"{node.start_point[0] + 1}>"
            )

        start_line = (
            node.start_point[0] + 1
        )

        end_line = (
            node.end_point[0] + 1
        )

        start_byte = node.start_byte
        end_byte = node.end_byte

        # --------------------------------------------------------------
        # Stable symbol identity
        #
        # DO NOT include start_line/end_line here.
        # --------------------------------------------------------------

        raw_id = (
            f"{result.path}:"
            f"{kind}:"
            f"{name}"
        )

        symbol_hash = hashlib.sha1(
            raw_id.encode("utf-8")
        ).hexdigest()[:20]

        symbol_id = (
            f"{kind}:{symbol_hash}"
        )

        return CodeSymbol(
            id=symbol_id,
            kind=kind,
            name=name,
            file=result.path,
            start_line=start_line,
            end_line=end_line,
            start_byte=start_byte,
            end_byte=end_byte,
            parent_id=parent_id,
            signature=self._signature(
                node,
                source,
            ),
        )

    # ------------------------------------------------------------------
    # Name extraction
    # ------------------------------------------------------------------

    def _extract_name(
        self,
        node,
        source: bytes,
    ) -> str:
        """Extract the declaration name.

        Tree-sitter grammars differ in their exact child structure,
        so this uses common identifier node types first and then a
        shallow recursive fallback.
        """

        identifier_types = {
            "identifier",
            "type_identifier",
            "field_identifier",
            "property_identifier",
            "name",
        }

        # --------------------------------------------------------------
        # Direct child
        # --------------------------------------------------------------

        for child in node.children:
            if child.type in identifier_types:
                return self._node_text(
                    child,
                    source,
                )

        # --------------------------------------------------------------
        # Shallow fallback
        # --------------------------------------------------------------

        for child in node.children:
            for grandchild in child.children:
                if grandchild.type in identifier_types:
                    return self._node_text(
                        grandchild,
                        source,
                    )

        return ""

    # ------------------------------------------------------------------
    # Signature extraction
    # ------------------------------------------------------------------

    def _signature(
        self,
        node,
        source: bytes,
    ) -> str:
        """Return a compact source signature."""

        text = self._node_text(
            node,
            source,
        )

        if not text:
            return ""

        first_line = (
            text.splitlines()[0]
            .strip()
        )

        # Prevent enormous declarations from becoming cognition
        # metadata blobs.
        if len(first_line) > 500:
            first_line = first_line[:500]

        return first_line


        # ------------------------------------------------------------------
    # Import extraction
    # ------------------------------------------------------------------

    def _extract_import_reference(
        self,
        node,
        source: bytes,
    ) -> tuple[str, list[str]]:
        """Extract a normalized import target and imported symbols.
        Examples:
            import pricing
                -> ("pricing", [])
            import pricing as p
                -> ("pricing", [])
            from pricing import price_total
                -> ("pricing", ["price_total"])
            from package.pricing import price_total
                -> ("package.pricing", ["price_total"])
            from pricing import price_total, calculate_tax
                -> ("pricing", ["price_total", "calculate_tax"])
        """
        module_name = ""
        imported_names: list[str] = []
        node_type = node.type

        # --------------------------------------------------------------
        # from X import Y
        # --------------------------------------------------------------
        if node_type == "import_from_statement":
            seen_import_keyword = False

            for child in node.children:
                child_type = child.type
                text = self._node_text(
                    child,
                    source,
                ).strip()

                if child_type == "import":
                    seen_import_keyword = True
                    continue

                if not seen_import_keyword:
                    if child_type in (
                        "dotted_name",
                        "relative_import",
                    ):
                        module_name = text
                    continue

                if child_type in (
                    "dotted_name",
                    "identifier",
                    "aliased_import",
                ):
                    imported_text = text

                    if child_type == "aliased_import":
                        # "price_total as pt"
                        imported_text = (
                            imported_text
                            .split(" as ", 1)[0]
                            .strip()
                        )

                    if imported_text:
                        imported_names.append(
                            imported_text
                        )

                elif child_type == "import_list":
                    for item in child.children:
                        if item.type in (
                            "dotted_name",
                            "identifier",
                            "aliased_import",
                        ):
                            imported_text = (
                                self._node_text(
                                    item,
                                    source,
                                ).strip()
                            )

                            if item.type == "aliased_import":
                                imported_text = (
                                    imported_text
                                    .split(" as ", 1)[0]
                                    .strip()
                                )

                            if imported_text:
                                imported_names.append(
                                    imported_text
                                )

            return module_name, imported_names

        # --------------------------------------------------------------
        # import X
        # --------------------------------------------------------------
        if node_type == "import_statement":
            for child in node.children:
                if child.type == "dotted_name":
                    return (
                        self._node_text(
                            child,
                            source,
                        ).strip(),
                        [],
                    )

                if child.type == "aliased_import":
                    text = self._node_text(
                        child,
                        source,
                    ).strip()

                    return (
                        text.split(
                            " as ",
                            1,
                        )[0].strip(),
                        [],
                    )

            return "", []

        # --------------------------------------------------------------
        # Generic import nodes for other languages
        # --------------------------------------------------------------
        text = self._node_text(
            node,
            source,
        ).strip()

        return text, []
    
    # ------------------------------------------------------------------
    # Call extraction
    # ------------------------------------------------------------------

    def _extract_call_target(
        self,
        node,
        source: bytes,
    ) -> str:
        """Extract the callable target from a call expression.

        Examples:

            calculate(x)
                -> calculate

            calculator.add(x)
                -> calculator.add

            foo.bar.baz(x)
                -> foo.bar.baz

        The argument list is intentionally excluded.
        """

        text = self._node_text(
            node,
            source,
        ).strip()

        if not text:
            return ""

        match = re.match(
            r"([A-Za-z_$][\w$]*"
            r"(?:\.[A-Za-z_$][\w$]*)*)",
            text,
        )

        if match:
            return match.group(1)

        return ""

    # ------------------------------------------------------------------
    # Node text
    # ------------------------------------------------------------------

    @staticmethod
    def _node_text(
        node,
        source: bytes,
    ) -> str:
        """Return the exact source text represented by a node."""

        return source[
            node.start_byte:node.end_byte
        ].decode(
            "utf-8",
            errors="replace",
        )


# ---------------------------------------------------------------------------
# Shared parser instance
# ---------------------------------------------------------------------------

_parser = CodeParser()


# ---------------------------------------------------------------------------
# Public helper
# ---------------------------------------------------------------------------

def parse_code(
    path: str | Path,
    source: str | bytes,
    language: str | None = None,
) -> ParsedFile:
    """Parse source code using the shared deterministic parser."""

    return _parser.parse_file(
        path=path,
        source=source,
        language=language,
    )