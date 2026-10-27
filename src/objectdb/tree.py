"""
Tree handle and page adapters for named B+ trees in ObjectDB.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from objectdb.record import decode_record, encode_record
from objectdb.transaction import TransactionState

if TYPE_CHECKING:
    from objectdb.database import ObjectDB
    from objectdb.persistent_btree import PersistentBTree

MIN_KEY = -9223372036854775808
MAX_KEY = 9223372036854775807


def _validate_key(key: int) -> None:
    if not isinstance(key, int) or isinstance(key, bool):
        raise TypeError("Key must be an integer")
    if not (MIN_KEY <= key <= MAX_KEY):
        raise ValueError(
            f"Key {key} out of range for 64-bit signed integer [{MIN_KEY}, {MAX_KEY}]"
        )


class NamedTreePageAdapter:
    """Page manager adapter for a named tree outside an explicit transaction."""

    def __init__(self, db: ObjectDB, tree_name: str):
        self.db = db
        self.tree_name = tree_name

    def read_page(self, page_id: int) -> bytes:
        return self.db.page_manager.read_page(page_id)

    def write_page(self, page_id: int, data: bytes) -> None:
        self.db.page_manager.write_page(page_id, data)

    def allocate_page(self) -> int:
        return self.db.page_manager.allocate_page()

    def update_root_page_id(self, root_page_id: int) -> None:
        self.db.catalog.update_root(
            self.tree_name,
            root_page_id,
            writer=self.db.page_manager,
        )
        if self.tree_name in self.db._trees:
            self.db._trees[self.tree_name].btree.root_page_id = root_page_id

    @property
    def file(self):
        return self.db.page_manager.file


class NamedTreeTransactionPageAdapter:
    """Page manager adapter for a named tree inside an active transaction."""

    def __init__(self, transaction, page_manager, tree: Tree):
        self.transaction = transaction
        self.page_manager = page_manager
        self.tree = tree

    def read_page(self, page_id: int) -> bytes:
        return self.page_manager.read_page(page_id)

    def write_page(self, page_id: int, data: bytes) -> None:
        self.transaction.write_page(page_id, data)

    def allocate_page(self) -> int:
        return self.transaction.allocate_page()

    def update_root_page_id(self, root_page_id: int) -> None:
        self.tree.db.catalog.update_root(
            self.tree.name,
            root_page_id,
            writer=self.transaction.page_writer,
            tx_id=self.transaction.transaction_id,
        )
        self.tree.btree.root_page_id = root_page_id

    @property
    def file(self):
        return self.page_manager.file


class Tree:
    """
    Handle for an independent, persistent named B+ tree inside an ObjectDB database.
    """

    def __init__(self, name: str, db: ObjectDB, btree: PersistentBTree):
        self._name = name
        self.db = db
        self.btree = btree

    @property
    def name(self) -> str:
        return self._name

    @property
    def root_page_id(self) -> int:
        return self.btree.root_page_id

    def _ensure_valid(self) -> None:
        self.db._ensure_open()
        if not self.db.catalog.has_tree(self._name):
            from objectdb.catalog import TreeNotFoundError
            raise TreeNotFoundError(
                f"Tree '{self._name}' has been dropped or does not exist"
            )

    def put(self, key: int, value: bytes) -> None:
        """Store key-value pair in this tree."""
        self._ensure_valid()
        _validate_key(key)

        if not isinstance(value, bytes):
            raise TypeError("Value must be bytes")

        with self.db._mutation_lock:
            if self.db._active_transaction is not None:
                offset, size = self.db.segment.append(value)
                self.db.segment.sync()
                record = encode_record(0, offset, size)
                self.btree.insert_into_root(key, record)
                return

            tx = None
            old_adapter = self.btree.page_manager
            try:
                tx = self.db._begin_transaction()
                adapter = NamedTreeTransactionPageAdapter(tx, self.db.page_manager, self)
                self.btree.page_manager = adapter

                offset, size = self.db.segment.append(value)
                self.db.segment.sync()
                record = encode_record(0, offset, size)
                self.btree.insert_into_root(key, record)
                tx.commit()
            except Exception:
                if (
                    tx is not None
                    and tx.state == TransactionState.ACTIVE
                    and not getattr(self.db.wal, "_is_broken", False)
                ):
                    try:
                        tx.rollback()
                    except Exception:
                        self.db.is_poisoned = True
                    finally:
                        if self.db.catalog is not None and self.db.catalog.has_tree(self._name):
                            self.btree.root_page_id = self.db.catalog.get_root(self._name)
                else:
                    self.db.is_poisoned = True
                raise
            finally:
                self.btree.page_manager = old_adapter

    def get(self, key: int) -> bytes | None:
        """Retrieve value for key from this tree. Returns None if missing."""
        self._ensure_valid()
        _validate_key(key)

        record = self.btree.search(key)
        if record is None:
            return None

        segment_id, offset, size = decode_record(record)
        if segment_id != 0:
            raise ValueError("Unknown segment ID")

        return self.db.segment.read(offset, size)

    def delete(self, key: int) -> bool:
        """Delete key from this tree. Returns True if deleted, False if key did not exist."""
        self._ensure_valid()
        _validate_key(key)

        with self.db._mutation_lock:
            if self.db._active_transaction is not None:
                return self.btree.delete(key)

            tx = None
            old_adapter = self.btree.page_manager
            try:
                tx = self.db._begin_transaction()
                adapter = NamedTreeTransactionPageAdapter(tx, self.db.page_manager, self)
                self.btree.page_manager = adapter

                deleted = self.btree.delete(key)
                tx.commit()
                return deleted
            except Exception:
                if (
                    tx is not None
                    and tx.state == TransactionState.ACTIVE
                    and not getattr(self.db.wal, "_is_broken", False)
                ):
                    try:
                        tx.rollback()
                    except Exception:
                        self.db.is_poisoned = True
                    finally:
                        if self.db.catalog is not None and self.db.catalog.has_tree(self._name):
                            self.btree.root_page_id = self.db.catalog.get_root(self._name)
                else:
                    self.db.is_poisoned = True
                raise
            finally:
                self.btree.page_manager = old_adapter

    def __repr__(self) -> str:
        return f"<Tree name={self._name!r} root_page_id={self.root_page_id}>"
