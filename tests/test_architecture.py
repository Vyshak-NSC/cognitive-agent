"""Fails the build on upward imports, cycles, or env names leaking out of the registry."""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

# Lowest first. A module may import only modules in a strictly lower layer.
LAYERS = [
    ["cpa", "cpa.providers.types", "cpa.providers.adapters"],
    ["cpa.providers.registry", "cpa.providers.util"],
    ["cpa.providers.wire_openai", "cpa.providers.wire_anthropic"],
    ["cpa.providers.adapters.base"],
    [
        "cpa.providers.adapters.azure",
        "cpa.providers.adapters.openai",
        "cpa.providers.adapters.anthropic",
        "cpa.providers.adapters.gemini",
        "cpa.providers.adapters.openrouter",
    ],
    ["cpa.providers.adapters.azure_openai", "cpa.providers.adapters.azure_anthropic"],
    ["cpa.providers.factory"],
    ["cpa.providers"],  # the package's public exports
    ["cpa.settings"],
    ["cpa.service"],
    ["cpa.api", "cpa.ui"],  # the two shells: neither may import the other
    ["cpa.cli"],
]
LEVEL = {name: i for i, layer in enumerate(LAYERS) for name in layer}


def modules() -> dict[str, Path]:
    found = {}
    for path in (SRC / "cpa").rglob("*.py"):
        rel = path.relative_to(SRC).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts.pop()
        found[".".join(parts)] = path
    return found


def internal_imports(name: str, path: Path, known: set[str]) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"{name}: relative imports are not allowed"
            base = node.module or ""
            for alias in node.names:
                full = f"{base}.{alias.name}"
                target = full if full in known else base
                if target in known:
                    out.add(target)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names if a.name in known)
    out.discard(name)
    return out


def test_every_module_has_a_layer():
    assert set(modules()) <= set(LEVEL), set(modules()) - set(LEVEL)


def test_imports_only_go_downwards():
    mods = modules()
    for name, path in mods.items():
        for target in internal_imports(name, path, set(mods)):
            assert LEVEL[target] < LEVEL[name], f"{name} must not import {target}"


def test_provider_env_names_live_only_in_the_registry():
    registry = (SRC / "cpa/providers/registry.py").read_text(encoding="utf-8")
    names = set(re.findall(r'"([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)"', registry))
    assert {"GEMINI_API_KEY", "OPENROUTER_ROUTING", "AZURE_FOUNDRY_ENDPOINT"} <= names
    for name, path in modules().items():
        if name == "cpa.providers.registry":
            continue
        text = path.read_text(encoding="utf-8")
        for env_name in names:
            assert env_name not in text, f"{env_name} appears in {name}; it belongs in the registry"
