from __future__ import annotations

import re

GENERATED_ID_RE = re.compile(r"^(?:entity|rel)_[0-9a-f]{40}$", re.IGNORECASE)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def is_generated_id(s: str) -> bool:
    return bool(GENERATED_ID_RE.fullmatch(s) or _UUID_RE.fullmatch(s))
