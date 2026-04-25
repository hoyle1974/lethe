from __future__ import annotations

import re
from pathlib import Path

_CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".java",
    ".go",
    ".rs",
    ".c",
    ".cpp",
    ".h",
    ".cs",
    ".rb",
    ".swift",
    ".kt",
}

_TOP_LEVEL_DEF = re.compile(r"^(def |class |async def )", re.MULTILINE)


def detect_chunk_strategy(filename: str) -> str:
    """Return 'code' or 'prose' based on file extension."""
    ext = Path(filename).suffix.lower()
    return "code" if ext in _CODE_EXTENSIONS else "prose"


def chunk_text(text: str, chunk_size: int = 600, overlap: int = 1) -> list[str]:
    """Split prose into overlapping paragraph-boundary chunks.

    chunk_size is measured in words (approximate tokens).
    overlap is the number of trailing paragraphs carried into the next chunk.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    window: list[str] = []
    size = 0

    for para in paragraphs:
        words = len(para.split())
        if size + words > chunk_size and window:
            chunks.append("\n\n".join(window))
            window = window[-overlap:]
            size = sum(len(p.split()) for p in window)
        window.append(para)
        size += words

    if window:
        chunks.append("\n\n".join(window))

    return chunks


def chunk_code(text: str, chunk_size: int = 600) -> list[str]:
    """Split source code at top-level def/class/async def boundaries.

    Each chunk includes the file preamble (imports, module-level code before
    the first definition). Falls back to prose chunking if no definitions found.
    """
    lines = text.splitlines(keepends=True)

    preamble_lines: list[str] = []
    blocks: list[list[str]] = []
    current: list[str] = []
    found_def = False

    for line in lines:
        if _TOP_LEVEL_DEF.match(line):
            if current:
                if found_def:
                    blocks.append(current)
                else:
                    preamble_lines = current
            current = [line]
            found_def = True
        else:
            current.append(line)

    if current:
        if found_def:
            blocks.append(current)
        else:
            preamble_lines = current

    if not blocks:
        return chunk_text(text, chunk_size)

    preamble = "".join(preamble_lines).strip()
    chunks: list[str] = []

    for i, block in enumerate(blocks):
        block_text = "".join(block).strip()
        # Include preamble only on first chunk so imports aren't re-extracted N times.
        prefix = preamble if (preamble and i == 0) else ""
        content = f"{prefix}\n\n{block_text}" if prefix else block_text
        if len(content.split()) > chunk_size * 2:
            sub_chunks = chunk_text(content, chunk_size)
            chunks.extend(sub_chunks)
        else:
            chunks.append(content)

    return chunks


def chunk_document(text: str, filename: str = "", chunk_size: int = 600) -> list[str]:
    """Chunk a document using the appropriate strategy for its file type."""
    if detect_chunk_strategy(filename) == "code":
        return chunk_code(text, chunk_size)
    return chunk_text(text, chunk_size)
