from __future__ import annotations

import ast
import re
from pathlib import Path

from lethe.graph.chunk import _CODE_EXTENSIONS

StructuralTriple = tuple[str, str, str]


def extract_structural_triples(text: str, filename: str) -> list[StructuralTriple]:
    """Return deterministic (subject, predicate, object) triples from source code.

    Predicates: 'imports', 'defines', 'has_method'.
    Returns [] for non-code file extensions.
    """
    ext = Path(filename).suffix.lower()
    if ext not in _CODE_EXTENSIONS:
        return []
    module_name = Path(filename).stem
    if ext == ".py":
        return _python_triples(text, module_name)
    return _generic_code_triples(text, module_name)


def _python_triples(text: str, module_name: str) -> list[StructuralTriple]:
    triples: list[StructuralTriple] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _generic_code_triples(text, module_name)
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                triples.append((module_name, "imports", alias.name.split(".")[0]))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                triples.append((module_name, "imports", node.module.split(".")[0]))
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            triples.append((module_name, "defines", node.name))
        elif isinstance(node, ast.ClassDef):
            triples.append((module_name, "defines", node.name))
            for item in ast.iter_child_nodes(node):
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    triples.append((node.name, "has_method", item.name))
    return triples


def _generic_code_triples(text: str, module_name: str) -> list[StructuralTriple]:
    triples: list[StructuralTriple] = []
    import_re = re.compile(r"""(?:import|require|from)\s*\(?['"]?([a-zA-Z0-9_/@.-]+)['"]?""")
    seen: set[str] = set()
    for m in import_re.finditer(text):
        name = m.group(1).split("/")[0].split(".")[0]
        if name and name not in seen:
            seen.add(name)
            triples.append((module_name, "imports", name))
    return triples
