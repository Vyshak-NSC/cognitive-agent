"""Deterministic source-code cognition compiler.

Code is parsed locally. No LLM calls are made here.

The compiler:
    1. parses every selected source-code file with Tree-sitter
    2. creates deterministic file/symbol entities
    3. records imports and calls
    4. resolves references between files where possible
    5. persists relationships with exact source-artifact provenance

Semantic interpretation belongs to the LLM compiler and is deliberately
kept separate from structural code extraction.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ragapp.cognition.code_parser import (
    CodeReference,
    CodeSymbol,
    ParsedFile,
    detect_language,
    parse_code,
)


class CodeCognitionCompiler:
    """Compile source code into deterministic cognition."""

    def __init__(self, store):
        self.store = store

    def compile_files(
        self,
        files: Iterable[str | Path],
    ) -> dict:
        paths = [Path(p) for p in files]

        parsed_files: list[ParsedFile] = []
        failed: list[dict] = []

        # ---------------------------------------------------------------
        # Parse everything first.
        #
        # We need the complete symbol table before resolving cross-file
        # references.
        # ---------------------------------------------------------------

        for path in paths:
            try:
                language = detect_language(path)

                if not language:
                    continue

                source = path.read_bytes()

                relative = self._relative_source_path(path)

                parsed = parse_code(
                    relative,
                    source,
                    language,
                )

                parsed_files.append(parsed)

            except Exception as exc:
                failed.append(
                    {
                        "file": str(path),
                        "error": str(exc),
                    }
                )

        # ---------------------------------------------------------------
        # Build deterministic indexes.
        # ---------------------------------------------------------------

        symbol_index = self._build_symbol_index(
            parsed_files
        )

        file_index = self._build_file_index(
            parsed_files
        )

        # ---------------------------------------------------------------
        # Persist files and symbols.
        # ---------------------------------------------------------------

        compiled = []

        for parsed in parsed_files:
            try:
                self._persist_file(
                    parsed,
                    symbol_index,
                    file_index,
                )

                compiled.append(parsed.path)

            except Exception as exc:
                failed.append(
                    {
                        "file": parsed.path,
                        "error": str(exc),
                    }
                )

        return {
            "compiled": compiled,
            "failed": failed,
            "files_parsed": len(parsed_files),
            "symbols": sum(
                len(p.symbols)
                for p in parsed_files
            ),
            "references": sum(
                len(p.references)
                for p in parsed_files
            ),
        }

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    def _relative_source_path(self, path: Path) -> str:
        """Return the path relative to the project's source directory."""

        source_root = self.store.source.resolve()
        absolute = path.resolve()

        try:
            return absolute.relative_to(
                source_root
            ).as_posix()
        except ValueError:
            # This should not normally happen because the compiler is
            # called with source files, but keep the fallback deterministic.
            return path.as_posix()

    # ------------------------------------------------------------------
    # Index construction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_symbol_index(
        parsed_files: list[ParsedFile],
    ) -> dict[str, list[CodeSymbol]]:
        """Index symbols by their simple name.

        Multiple files can legally contain the same name, therefore the
        value is a list rather than a single symbol.
        """
        index: dict[str, list[CodeSymbol]] = {}

        for parsed in parsed_files:
            for symbol in parsed.symbols:
                index.setdefault(
                    symbol.name,
                    [],
                ).append(symbol)

        return index

    @staticmethod
    def _build_file_index(
        parsed_files: list[ParsedFile],
    ) -> dict[str, ParsedFile]:
        return {
            parsed.path: parsed
            for parsed in parsed_files
        }

    # ------------------------------------------------------------------
    # File persistence
    # ------------------------------------------------------------------

    def _persist_file(
        self,
        parsed: ParsedFile,
        symbol_index: dict[str, list[CodeSymbol]],
        file_index: dict[str, ParsedFile],
    ):
        artifact_id = f"source:{parsed.path}"

        # ---------------------------------------------------------------
        # File entity.
        # ---------------------------------------------------------------

        self.store.upsert_entity_update(
            {
                "id": artifact_id,
                "type": "file",
                "name": parsed.path,
                "summary": (
                    f"{parsed.language} source file "
                    f"containing {len(parsed.symbols)} symbols."
                ),
                "tags": [
                    "source",
                    "code",
                    parsed.language,
                ],
                "attributes": {
                    "language": parsed.language,
                    "path": parsed.path,
                    "symbol_count": len(
                        parsed.symbols
                    ),
                    "import_count": len(
                        parsed.imports
                    ),
                    "reference_count": len(
                        parsed.references
                    ),
                    "syntax_errors": list(
                        parsed.errors
                    ),
                },
            },
            source_artifact=artifact_id,
        )

        # ---------------------------------------------------------------
        # Symbols.
        # ---------------------------------------------------------------

        for symbol in parsed.symbols:
            self._persist_symbol(
                symbol,
                parsed,
                artifact_id,
            )

        # ---------------------------------------------------------------
        # File -> symbol relationships.
        # ---------------------------------------------------------------

        for symbol in parsed.symbols:
            self.store.add_relationship(
                {
                    "id": (
                        f"defined_in:"
                        f"{symbol.id}:"
                        f"{artifact_id}"
                    ),
                    "source": symbol.id,
                    "target": artifact_id,
                    "type": "defined_in",
                    "state": "active",
                },
                timeline="source",
                source_artifact=artifact_id,
            )

            if symbol.parent_id:
                self.store.add_relationship(
                    {
                        "id": (
                            f"member_of:"
                            f"{symbol.id}:"
                            f"{symbol.parent_id}"
                        ),
                        "source": symbol.id,
                        "target": symbol.parent_id,
                        "type": "member_of",
                        "state": "active",
                    },
                    timeline="source",
                    source_artifact=artifact_id,
                )

        # ---------------------------------------------------------------
        # Imports / calls / references.
        # ---------------------------------------------------------------

        for reference in parsed.references:
            self._persist_reference(
                reference,
                parsed,
                artifact_id,
                symbol_index,
                file_index,
            )

    # ------------------------------------------------------------------
    # Symbol persistence
    # ------------------------------------------------------------------

    def _persist_symbol(
        self,
        symbol: CodeSymbol,
        parsed: ParsedFile,
        artifact_id: str,
    ):
        qualified_name = self._qualified_name(
            symbol,
            parsed,
        )

        self.store.upsert_entity_update(
            {
                "id": symbol.id,
                "type": symbol.kind,
                "name": symbol.name,
                "summary": (
                    f"{symbol.kind} "
                    f"{qualified_name} "
                    f"in {parsed.path}."
                ),
                "tags": [
                    "code",
                    parsed.language,
                    symbol.kind,
                ],
                "attributes": {
                    "language": parsed.language,
                    "file": parsed.path,
                    "qualified_name": qualified_name,
                    "start_line": symbol.start_line,
                    "end_line": symbol.end_line,
                    "start_byte": symbol.start_byte,
                    "end_byte": symbol.end_byte,
                    "signature": symbol.signature,
                    "parent_id": symbol.parent_id,
                },
            },
            source_artifact=artifact_id,
        )

    @staticmethod
    def _qualified_name(
        symbol: CodeSymbol,
        parsed: ParsedFile,
    ) -> str:
        # Walk parent links.
        by_id = {
            s.id: s
            for s in parsed.symbols
        }

        names = [symbol.name]
        current = symbol

        while current.parent_id:
            parent = by_id.get(
                current.parent_id
            )

            if not parent:
                break

            names.append(parent.name)
            current = parent

        names.reverse()

        return ".".join(names)

    # ------------------------------------------------------------------
    # Reference persistence
    # ------------------------------------------------------------------

    def _persist_reference(
        self,
        reference: CodeReference,
        parsed: ParsedFile,
        artifact_id: str,
        symbol_index: dict[str, list[CodeSymbol]],
        file_index: dict[str, ParsedFile],
    ):
        source_id = (
            reference.source_symbol
            or artifact_id
        )

        targets = self._resolve_reference(
            reference,
            parsed,
            symbol_index,
            file_index,
        )

        # ---------------------------------------------------------------
        # We can resolve a reference to one or more concrete entities.
        # ---------------------------------------------------------------

        if targets:
            for target_id, relation_type in targets:
                self.store.add_relationship(
                    {
                        "id": (
                            f"{relation_type}:"
                            f"{source_id}:"
                            f"{target_id}"
                        ),
                        "source": source_id,
                        "target": target_id,
                        "type": relation_type,
                        "state": "active",
                    },
                    timeline="source",
                    source_artifact=artifact_id,
                )

            return

        # ---------------------------------------------------------------
        # If we cannot resolve it, preserve the structural fact rather
        # than inventing a target.
        #
        # The unresolved target is kept as an attribute on a relationship
        # whose target is the containing file.
        # ---------------------------------------------------------------

        self.store.add_relationship(
            {
                "id": (
                    f"unresolved:"
                    f"{source_id}:"
                    f"{reference.kind}:"
                    f"{reference.target_name}:"
                    f"{reference.start_line}"
                ),
                "source": source_id,
                "target": artifact_id,
                "type": f"unresolved_{reference.kind}",
                "state": "unresolved",
                "description": reference.target_name,
            },
            timeline="source",
            source_artifact=artifact_id,
        )

    
    @staticmethod
    def _normalize_module_name(value: str) -> str:
        """Normalize a source path or Python module name.
        Examples:
            pricing.py
                -> pricing
            pricing
                -> pricing
            package/pricing.py
                -> package.pricing
            package\\pricing.py
                -> package.pricing
            package.pricing
                -> package.pricing
            ./pricing.py
                -> pricing
        """
        value = str(value).strip()
        value = value.replace("\\", "/")
        value = value.replace("::", ".")
        value = value.replace("/", ".")
        
        while value.startswith("./"):
            value = value[2:]
        
        if value.endswith(".py"):
            value = value[:-3]
        
        while value.startswith("."):
            value = value[1:]
        
        return value.strip(".")
    # ------------------------------------------------------------------
    # Reference resolution
    # ------------------------------------------------------------------

    def _resolve_reference(
        self,
        reference: CodeReference,
        parsed: ParsedFile,
        symbol_index: dict[str, list[CodeSymbol]],
        file_index: dict[str, ParsedFile],
    ) -> list[tuple[str, str]]:
        target = reference.target_name.strip()

        if not target:
            return []

        # ---------------------------------------------------------------
        # Imports resolve to modules/files first.
        #
        # Example:
        #     from pricing import price_total
        #
        # Parser produces:
        #     target_name = "pricing"
        #
        # Therefore this must resolve to:
        #     source:pricing.py
        #
        # before attempting symbol-name resolution.
        # ---------------------------------------------------------------

        if reference.kind == "import":
            normalized_target = (
                self._normalize_module_name(
                    target
                )
            )

            file_candidates = []

            for file_path in file_index:
                normalized_file = (
                    self._normalize_module_name(
                        file_path
                    )
                )

                if normalized_target == normalized_file:
                    file_candidates.append(
                        file_path
                    )

            if len(file_candidates) == 1:
                return [
                    (
                        f"source:{file_candidates[0]}",
                        "dependency",
                    )
                ]
        results: list[tuple[str, str]] = []

        # ---------------------------------------------------------------
        # 1. Exact qualified-name match.
        # ---------------------------------------------------------------

        qualified_matches = []

        for symbols in symbol_index.values():
            for symbol in symbols:
                qualified = self._qualified_name_from_all_files(
                    symbol,
                    file_index,
                )

                if (
                    qualified == target
                    or qualified.endswith(
                        "." + target
                    )
                ):
                    qualified_matches.append(symbol)

        if qualified_matches:
            relation_type = self._relation_type(
                reference.kind
            )

            return [
                (
                    symbol.id,
                    relation_type,
                )
                for symbol in qualified_matches
            ]

        # ---------------------------------------------------------------
        # 2. Simple-name match.
        #
        # Only resolve if there is a unique candidate.
        # Never guess when multiple symbols have the same name.
        # ---------------------------------------------------------------

        simple_name = target.split(".")[-1]

        candidates = symbol_index.get(
            simple_name,
            [],
        )

        if len(candidates) == 1:
            return [
                (
                    candidates[0].id,
                    self._relation_type(
                        reference.kind
                    ),
                )
            ]

        
        return []

    @staticmethod
    def _relation_type(kind: str) -> str:
        if kind == "import":
            return "dependency"

        if kind == "call":
            return "calls"

        return "references"

    @staticmethod
    def _qualified_name_from_all_files(
        symbol: CodeSymbol,
        file_index: dict[str, ParsedFile],
    ) -> str:
        parsed = file_index.get(
            symbol.file
        )

        if not parsed:
            return symbol.name

        return CodeCognitionCompiler._qualified_name(
            symbol,
            parsed,
        )