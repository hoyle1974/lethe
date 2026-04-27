from dataclasses import dataclass, field

from google.cloud import firestore

from lethe.constants import (
    DEFAULT_NODE_TYPE,
    NODE_TYPE_LOG,
)
from lethe.infra.fs_helpers import ArrayUnion

CONFIG_COLLECTION = "_config"
CANONICAL_MAP_DOC = "canonical_map"

DEFAULT_NODE_TYPES = [
    "person",
    "place",
    "event",
    "project",
    "goal",
    "preference",
    "asset",
    "tool",
    DEFAULT_NODE_TYPE,
    NODE_TYPE_LOG,
]

DEFAULT_PREDICATES = [
    "works_at",
    "lives_in",
    "knows",
    "is_part_of",
    "owns",
    "uses",
    "participates_in",
    "located_at",
    "created_by",
    "manages",
    "reports_to",
    "related_to",
    "is_a",
]


@dataclass
class CanonicalMap:
    node_types: list[str] = field(default_factory=lambda: list(DEFAULT_NODE_TYPES))
    allowed_predicates: list[str] = field(default_factory=lambda: list(DEFAULT_PREDICATES))


async def load_canonical_map(db: firestore.AsyncClient) -> CanonicalMap:
    ref = db.collection(CONFIG_COLLECTION).document(CANONICAL_MAP_DOC)
    doc = await ref.get()
    if not doc.exists:
        return CanonicalMap()
    data = doc.to_dict() or {}
    node_types = data.get("node_types") or DEFAULT_NODE_TYPES
    predicates = data.get("allowed_predicates") or DEFAULT_PREDICATES
    return CanonicalMap(node_types=list(node_types), allowed_predicates=list(predicates))


async def seed_canonical_map(db: firestore.AsyncClient) -> None:
    """Write defaults if the doc does not exist yet."""
    ref = db.collection(CONFIG_COLLECTION).document(CANONICAL_MAP_DOC)
    doc = await ref.get()
    if not doc.exists:
        await ref.set(
            {
                "node_types": DEFAULT_NODE_TYPES,
                "allowed_predicates": DEFAULT_PREDICATES,
            }
        )


async def append_predicate(db: firestore.AsyncClient, predicate: str) -> None:
    ref = db.collection(CONFIG_COLLECTION).document(CANONICAL_MAP_DOC)
    await ref.set({"allowed_predicates": ArrayUnion([predicate])}, merge=True)
