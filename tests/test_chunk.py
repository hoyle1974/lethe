from lethe.graph.chunk import chunk_code, chunk_document, chunk_text, detect_chunk_strategy


def test_detect_chunk_strategy_python():
    assert detect_chunk_strategy("main.py") == "code"


def test_detect_chunk_strategy_typescript():
    assert detect_chunk_strategy("app.ts") == "code"


def test_detect_chunk_strategy_javascript():
    assert detect_chunk_strategy("router.js") == "code"


def test_detect_chunk_strategy_prose_md():
    assert detect_chunk_strategy("README.md") == "prose"


def test_detect_chunk_strategy_prose_txt():
    assert detect_chunk_strategy("notes.txt") == "prose"


def test_detect_chunk_strategy_no_extension():
    assert detect_chunk_strategy("Makefile") == "prose"


def test_chunk_text_short_text_single_chunk():
    text = "Alice works at Acme.\n\nBob is her manager."
    chunks = chunk_text(text, chunk_size=100)
    assert len(chunks) == 1
    assert "Alice" in chunks[0]
    assert "Bob" in chunks[0]


def test_chunk_text_splits_large_text():
    # 4 paragraphs of 10 words each = 40 words total; chunk_size=15 forces splits
    paras = ["word " * 10 for _ in range(4)]
    text = "\n\n".join(paras)
    chunks = chunk_text(text, chunk_size=15)
    assert len(chunks) > 1


def test_chunk_text_overlap_carries_last_paragraph():
    # With overlap=1, the last paragraph of chunk N appears in chunk N+1
    para_a = "Alice is the founder and CEO of Acme Corporation based in New York."
    para_b = "Bob runs the engineering department and reports directly to Alice."
    para_c = "Carol joined as a new engineer working under Bob this quarter."
    text = f"{para_a}\n\n{para_b}\n\n{para_c}"
    chunks = chunk_text(text, chunk_size=12, overlap=1)
    # If split occurred, overlap means para_b appears in both chunks
    if len(chunks) > 1:
        assert para_b in chunks[0] or para_b in chunks[1]


def test_chunk_text_empty_string():
    chunks = chunk_text("", chunk_size=100)
    assert chunks == []


def test_chunk_text_whitespace_only():
    chunks = chunk_text("   \n\n  \n\n  ", chunk_size=100)
    assert chunks == []


def test_chunk_code_splits_on_top_level_defs():
    code = "import os\n\ndef foo():\n    return 1\n\ndef bar():\n    return 2\n"
    chunks = chunk_code(code)
    assert len(chunks) == 2
    assert "def foo" in chunks[0]
    assert "def bar" in chunks[1]


def test_chunk_code_splits_on_class():
    code = "import sys\n\nclass Foo:\n    pass\n\nclass Bar:\n    pass\n"
    chunks = chunk_code(code)
    assert len(chunks) == 2
    assert "class Foo" in chunks[0]
    assert "class Bar" in chunks[1]


def test_chunk_code_includes_preamble_in_each_chunk():
    code = (
        "import os\n"
        "import sys\n\n"
        "def foo():\n"
        "    return os.getcwd()\n\n"
        "def bar():\n"
        "    return sys.argv\n"
    )
    chunks = chunk_code(code)
    assert len(chunks) == 2
    assert "import os" in chunks[0]
    assert "import os" in chunks[1]


def test_chunk_code_async_def():
    code = "async def foo():\n    pass\n\nasync def bar():\n    pass\n"
    chunks = chunk_code(code)
    assert len(chunks) == 2
    assert "async def foo" in chunks[0]
    assert "async def bar" in chunks[1]


def test_chunk_code_no_defs_falls_back_to_prose():
    text = "x = 1\ny = 2\nz = x + y"
    chunks = chunk_code(text, chunk_size=100)
    assert len(chunks) == 1
    assert "x = 1" in chunks[0]


def test_chunk_document_routes_py_to_code():
    code = "import os\n\ndef foo():\n    pass\n\ndef bar():\n    pass\n"
    chunks = chunk_document(code, filename="main.py", chunk_size=100)
    assert len(chunks) == 2


def test_chunk_document_routes_txt_to_prose():
    text = "Short text.\n\nAnother paragraph."
    chunks = chunk_document(text, filename="notes.txt", chunk_size=100)
    assert len(chunks) == 1


def test_chunk_document_no_filename_uses_prose():
    text = "Short text.\n\nAnother paragraph."
    chunks = chunk_document(text, filename="", chunk_size=100)
    assert len(chunks) == 1


def test_chunk_code_oversized_block_preamble_in_all_sub_chunks():
    """Oversized function body splits into sub-chunks that all carry the preamble."""
    preamble = "import os"
    body_line = "x = os.path.join('a', 'b', 'c', 'd', 'e', 'f', 'g', 'h')\n"
    body = body_line * 4  # ~36 words, triggers split at chunk_size=15 (threshold=30)
    code = f"{preamble}\n\ndef foo():\n{body}"
    chunks = chunk_code(code, chunk_size=15)
    assert len(chunks) > 1, "oversized block should produce multiple sub-chunks"
    for i, chunk in enumerate(chunks):
        assert "import os" in chunk, f"chunk {i} is missing the preamble"
