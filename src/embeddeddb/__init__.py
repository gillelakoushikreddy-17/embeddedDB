"""EmbeddedDB: A lightweight, reliable embedded key-value database engine."""

from embeddeddb.catalog import TreeAlreadyExistsError, TreeNotFoundError
from embeddeddb.database import EmbeddedDB, EmbeddedDB
from embeddeddb.tree import Tree

__all__ = [
    "EmbeddedDB",
    "EmbeddedDB",
    "Tree",
    "TreeAlreadyExistsError",
    "TreeNotFoundError",
]
