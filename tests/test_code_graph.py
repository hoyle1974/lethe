from lethe.graph.code_graph import extract_structural_triples


def test_python_import_creates_imports_triple():
    code = "import os\n\ndef foo():\n    pass\n"
    triples = extract_structural_triples(code, "utils.py")
    assert ("utils", "imports", "os") in triples


def test_python_from_import_uses_top_level_module():
    code = "from pathlib import Path\n\ndef foo():\n    pass\n"
    triples = extract_structural_triples(code, "main.py")
    assert ("main", "imports", "pathlib") in triples


def test_python_dotted_import_uses_top_level_only():
    code = "from google.cloud.firestore import Client\n\ndef foo():\n    pass\n"
    triples = extract_structural_triples(code, "db.py")
    objects = [o for _, p, o in triples if p == "imports"]
    assert "google" in objects
    assert "google.cloud.firestore" not in objects


def test_python_defines_function():
    code = "def my_function():\n    pass\n"
    triples = extract_structural_triples(code, "funcs.py")
    assert ("funcs", "defines", "my_function") in triples


def test_python_async_def_defines():
    code = "async def fetch_data():\n    pass\n"
    triples = extract_structural_triples(code, "fetch.py")
    assert ("fetch", "defines", "fetch_data") in triples


def test_python_defines_class():
    code = "class MyModel:\n    pass\n"
    triples = extract_structural_triples(code, "models.py")
    assert ("models", "defines", "MyModel") in triples


def test_python_class_method_creates_has_method_triple():
    code = "class Foo:\n    def bar(self):\n        pass\n    def baz(self):\n        pass\n"
    triples = extract_structural_triples(code, "foo.py")
    assert ("Foo", "has_method", "bar") in triples
    assert ("Foo", "has_method", "baz") in triples


def test_non_code_file_returns_empty():
    triples = extract_structural_triples("Some prose text.", "README.md")
    assert triples == []


def test_txt_file_returns_empty():
    triples = extract_structural_triples("import os", "notes.txt")
    assert triples == []


def test_syntax_error_falls_back_to_regex_for_imports():
    # Missing colon on if statement is a SyntaxError
    code = "import os\nif True\n    pass\n"
    triples = extract_structural_triples(code, "broken.py")
    objects = [o for _, p, o in triples if p == "imports"]
    assert "os" in objects


def test_js_file_extracts_imports_via_regex():
    code = "import React from 'react';\nimport { useState } from 'react';"
    triples = extract_structural_triples(code, "app.js")
    objects = [o for _, p, o in triples if p == "imports"]
    assert "react" in objects


def test_js_require_extracts_import():
    code = "const express = require('express');"
    triples = extract_structural_triples(code, "server.js")
    objects = [o for _, p, o in triples if p == "imports"]
    assert "express" in objects


def test_multiple_same_imports_no_crash():
    code = "import os\nimport os\n\ndef foo():\n    pass\n"
    triples = extract_structural_triples(code, "dup.py")
    imports = [o for _, p, o in triples if p == "imports" and o == "os"]
    assert len(imports) >= 1
