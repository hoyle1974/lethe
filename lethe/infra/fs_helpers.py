"""
Firestore helper imports — centralises version-specific import paths.
Use these instead of `firestore.Vector` / `firestore.ArrayUnion` directly.
"""

from google.cloud.firestore_v1 import ArrayUnion, FieldFilter
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
from google.cloud.firestore_v1.vector import Vector

__all__ = ["Vector", "DistanceMeasure", "ArrayUnion", "FieldFilter"]
