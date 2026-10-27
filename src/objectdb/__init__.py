"""ObjectDB: A lightweight, reliable embedded key-value database engine."""

from objectdb.catalog import TreeAlreadyExistsError, TreeNotFoundError
from objectdb.database import ObjectDB
from objectdb.tree import Tree

__version__ = "0.1.0"

__all__ = [
    "ObjectDB",
    "Tree",
    "TreeAlreadyExistsError",
    "TreeNotFoundError",
    "__version__",
]