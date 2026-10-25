import fcntl
import os
from pathlib import Path
import shutil
import struct

from objectdb.header import NO_ROOT_PAGE, create_header, read_header
from objectdb.page_manager import PAGE_SIZE, PageManager
from objectdb.persistent_btree import PersistentBTree
from objectdb.record import decode_record, encode_record
from objectdb.recovery import RecoveryManager
from objectdb.segment import Segment
from objectdb.transaction import TransactionPageAdapter, TransactionState
from objectdb.transactional_page_writer import TransactionalPageWriter
from objectdb.wal import WriteAheadLog

MIN_KEY = -9223372036854775808
MAX_KEY = 9223372036854775807

_ACTIVE_INSTANCES = {}


def _validate_key(key):
    if not isinstance(key, int) or isinstance(key, bool):
        raise TypeError("Key must be an integer")
    if not (MIN_KEY <= key <= MAX_KEY):
        raise ValueError(
            f"Key {key} out of range for 64-bit signed integer [{MIN_KEY}, {MAX_KEY}]"
        )


class ObjectDB:
    def __init__(self, path):
        self.path = Path(path)
        self.page_manager = None
        self.tree = None
        self.segment = None
        self.wal = None
        self.page_writer = None
        self.next_transaction_id = 1
        self.is_open = False
        self.is_poisoned = False
        self._active_transaction = None
        self._lock_fp = None

    def __enter__(self):
        if not self.is_open:
            self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        if hasattr(self, "_lock_fp") and self._lock_fp is not None:
            try:
                fcntl.flock(self._lock_fp.fileno(), fcntl.LOCK_UN)
                self._lock_fp.close()
            except Exception:
                pass
            self._lock_fp = None

    def open(self):
        if self.is_open:
            raise RuntimeError("Database is already open")

        self.path.mkdir(parents=True, exist_ok=True)

        canonical_path = str(self.path.resolve())
        existing = _ACTIVE_INSTANCES.get(canonical_path)
        if existing is not None and existing is not self:
            if existing.is_open:
                raise RuntimeError(
                    "Database is already open by another instance in this process"
                )
            if existing._lock_fp is not None:
                try:
                    fcntl.flock(existing._lock_fp.fileno(), fcntl.LOCK_UN)
                    existing._lock_fp.close()
                except Exception:
                    pass
                existing._lock_fp = None

        # Acquire an exclusive advisory process lock on objectdb.lock.
        lock_file = self.path / "objectdb.lock"
        lock_fp = open(lock_file, "a+b")
        try:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            lock_fp.close()
            raise RuntimeError("Database is locked by another process") from exc

        self._lock_fp = lock_fp
        _ACTIVE_INSTANCES[canonical_path] = self

        db_file = self.path / "objectdb.data"
        segment_file = self.path / "segment-0.dat"
        wal_file = self.path / "objectdb.wal"

        if not db_file.exists():
            manager = PageManager(db_file)
            manager.initialize()

        manager = PageManager(db_file)
        manager.open()

        wal = WriteAheadLog(wal_file)
        segment = None

        try:
            wal.open()

            # Recover incomplete transactions before loading the tree.
            page_writer = TransactionalPageWriter(manager, wal)
            recovery = RecoveryManager(wal)
            recovery.recover(page_writer)

            analysis = recovery.analyze()
            existing_tx_ids = set(analysis["transactions"].keys())
            self.next_transaction_id = max(existing_tx_ids, default=0) + 1

            tree = PersistentBTree(manager)

            try:
                tree.load_existing_tree()
            except ValueError as exc:
                header = read_header(manager.read_page(0))

                if header["root_page_id"] != NO_ROOT_PAGE:
                    raise exc

                tree.create_empty_tree()

            segment = Segment(segment_file)
            segment.open()

        except Exception:
            if segment is not None:
                try:
                    segment.close()
                except Exception:
                    pass
            manager.close()
            wal.close()
            if self._lock_fp is not None:
                try:
                    fcntl.flock(self._lock_fp.fileno(), fcntl.LOCK_UN)
                    self._lock_fp.close()
                except Exception:
                    pass
                self._lock_fp = None
            raise

        self.page_manager = manager
        self.tree = tree
        self.segment = segment
        self.wal = wal
        self.page_writer = page_writer
        self.is_open = True
        self.is_poisoned = False

    def _begin_transaction(self):
        from objectdb.transaction import Transaction

        tx_id = self.next_transaction_id
        self.next_transaction_id += 1
        return Transaction(tx_id, self.wal, self.page_writer)

    def transaction(self):
        """Context manager for grouping multiple operations in a transaction."""
        return _DatabaseTransactionContext(self)

    def put(self, key: int, value: bytes):
        self._ensure_open()
        _validate_key(key)

        if not isinstance(value, bytes):
            raise TypeError("Value must be bytes")

        if self._active_transaction is not None:
            # Durability Ordering: payload is appended and flushed/synced to disk
            offset, size = self.segment.append(value)
            self.segment.sync()
            record = encode_record(0, offset, size)
            self.tree.insert_into_root(key, record)
            return

        tx = self._begin_transaction()
        adapter = TransactionPageAdapter(tx, self.page_manager, self.tree)
        old_adapter = self.tree.page_manager
        self.tree.page_manager = adapter

        try:
            # Durability Ordering:
            # 1. Append payload to segment
            # 2. Flush & fsync segment payload before recording index entry
            # 3. Apply metadata changes through transaction-aware B+ tree
            # 4. Flush & fsync pages & append COMMIT record in tx.commit()
            offset, size = self.segment.append(value)
            self.segment.sync()
            record = encode_record(0, offset, size)
            self.tree.insert_into_root(key, record)
            tx.commit()
        except Exception:
            if tx.state == TransactionState.ACTIVE:
                try:
                    tx.rollback()
                finally:
                    header = read_header(self.page_manager.read_page(0))
                    self.tree.root_page_id = header["root_page_id"]
            else:
                self.is_poisoned = True
            raise
        finally:
            self.tree.page_manager = old_adapter

    def delete(self, key: int) -> bool:
        self._ensure_open()
        _validate_key(key)

        if self._active_transaction is not None:
            return self.tree.delete(key)

        tx = self._begin_transaction()
        adapter = TransactionPageAdapter(tx, self.page_manager, self.tree)
        old_adapter = self.tree.page_manager
        self.tree.page_manager = adapter

        try:
            deleted = self.tree.delete(key)
            tx.commit()
            return deleted
        except Exception:
            if tx.state == TransactionState.ACTIVE:
                try:
                    tx.rollback()
                finally:
                    header = read_header(self.page_manager.read_page(0))
                    self.tree.root_page_id = header["root_page_id"]
            else:
                self.is_poisoned = True
            raise
        finally:
            self.tree.page_manager = old_adapter

    def get(self, key: int):
        self._ensure_open()
        _validate_key(key)

        record = self.tree.search(key)

        if record is None:
            return None

        segment_id, offset, size = decode_record(record)

        if segment_id != 0:
            raise ValueError("Unknown segment ID")

        return self.segment.read(offset, size)

    def check_integrity(self, raise_on_error: bool = False) -> dict:
        """
        Validate tree integrity, reachable pages, child bounds, key ordering,
        and segment offsets without modifying database files.
        """
        self._ensure_open()

        errors = []
        total_keys = 0
        reachable_pages = set()

        from objectdb.btree import (
            INTERNAL_NODE_TYPE,
            LEAF_NODE_TYPE,
            decode_internal_node,
            decode_leaf_node,
        )

        # 1. Header and file size validation
        try:
            header_page = self.page_manager.read_page(0)
            header = read_header(header_page)
            root_page_id = header["root_page_id"]
        except Exception as exc:
            errors.append(f"Invalid header page: {exc}")
            root_page_id = NO_ROOT_PAGE

        self.page_manager.file.seek(0, 2)
        db_file_size = self.page_manager.file.tell()
        if db_file_size % PAGE_SIZE != 0:
            errors.append(
                f"Database file size ({db_file_size}) is not a multiple of PAGE_SIZE ({PAGE_SIZE})"
            )
        total_pages = db_file_size // PAGE_SIZE

        self.segment.file.seek(0, 2)
        segment_size = self.segment.file.tell()

        # 2. Traverse B+ tree from root
        if root_page_id != NO_ROOT_PAGE:
            if root_page_id >= total_pages or root_page_id < 1:
                errors.append(
                    f"Root page ID {root_page_id} is out of bounds [1, {total_pages - 1}]"
                )
            else:
                reachable_pages.add(0)  # Header page
                queue = [(root_page_id, None, None, 0)]  # (page_id, min_key, max_key, depth)
                leaf_depths = set()

                while queue:
                    page_id, min_bound, max_bound, depth = queue.pop(0)

                    if page_id in reachable_pages:
                        errors.append(
                            f"Cycle detected: page {page_id} visited more than once"
                        )
                        continue

                    reachable_pages.add(page_id)

                    try:
                        page_data = self.page_manager.read_page(page_id)
                    except Exception as exc:
                        errors.append(f"Failed to read page {page_id}: {exc}")
                        continue

                    node_type, _ = struct.unpack_from("<BH", page_data, 0)

                    if node_type == LEAF_NODE_TYPE:
                        leaf_depths.add(depth)
                        try:
                            keys, records = decode_leaf_node(page_data)
                        except Exception as exc:
                            errors.append(f"Leaf page {page_id} decode error: {exc}")
                            continue

                        if len(keys) != len(records):
                            errors.append(
                                f"Leaf page {page_id} has mismatched keys ({len(keys)}) and records ({len(records)})"
                            )

                        total_keys += len(keys)

                        # Check keys strictly sorted
                        for i in range(len(keys) - 1):
                            if keys[i] >= keys[i + 1]:
                                errors.append(
                                    f"Leaf page {page_id} keys not strictly sorted: {keys[i]} >= {keys[i+1]}"
                                )

                        # Check bounds
                        if min_bound is not None and keys and keys[0] < min_bound:
                            errors.append(
                                f"Leaf page {page_id} has key {keys[0]} below min bound {min_bound}"
                            )
                        if max_bound is not None and keys and keys[-1] >= max_bound:
                            errors.append(
                                f"Leaf page {page_id} has key {keys[-1]} exceeding max bound {max_bound}"
                            )

                        # Validate records
                        for record in records:
                            try:
                                seg_id, offset, size = decode_record(record)
                                if seg_id != 0:
                                    errors.append(
                                        f"Leaf page {page_id} record references invalid segment ID {seg_id}"
                                    )
                                if offset + size > segment_size:
                                    errors.append(
                                        f"Leaf page {page_id} record range [{offset}, {offset+size}) exceeds segment size {segment_size}"
                                    )
                            except Exception as exc:
                                errors.append(f"Leaf page {page_id} record decode error: {exc}")

                    elif node_type == INTERNAL_NODE_TYPE:
                        try:
                            keys, child_ids = decode_internal_node(page_data)
                        except Exception as exc:
                            errors.append(f"Internal page {page_id} decode error: {exc}")
                            continue

                        if len(child_ids) != len(keys) + 1:
                            errors.append(
                                f"Internal page {page_id} child count {len(child_ids)} != key count {len(keys)} + 1"
                            )

                        # Check keys strictly sorted
                        for i in range(len(keys) - 1):
                            if keys[i] >= keys[i + 1]:
                                errors.append(
                                    f"Internal page {page_id} keys not strictly sorted: {keys[i]} >= {keys[i+1]}"
                                )

                        # Check bounds
                        if min_bound is not None and keys and keys[0] < min_bound:
                            errors.append(
                                f"Internal page {page_id} separator {keys[0]} below min bound {min_bound}"
                            )
                        if max_bound is not None and keys and keys[-1] >= max_bound:
                            errors.append(
                                f"Internal page {page_id} separator {keys[-1]} exceeding max bound {max_bound}"
                            )

                        # Check child bounds & enqueue
                        for i, child_id in enumerate(child_ids):
                            if child_id <= 0 or child_id >= total_pages:
                                errors.append(
                                    f"Internal page {page_id} has child {child_id} out of bounds [1, {total_pages - 1}]"
                                )
                                continue

                            child_min = min_bound if i == 0 else keys[i - 1]
                            child_max = keys[i] if i < len(keys) else max_bound
                            queue.append((child_id, child_min, child_max, depth + 1))
                    else:
                        errors.append(f"Page {page_id} has invalid node type {node_type}")

                if len(leaf_depths) > 1:
                    errors.append(
                        f"B+ tree is unbalanced: leaves found at varying depths {sorted(leaf_depths)}"
                    )

        result = {
            "valid": len(errors) == 0,
            "total_pages": total_pages,
            "reachable_pages": len(reachable_pages),
            "total_keys": total_keys,
            "errors": errors,
        }

        if raise_on_error and errors:
            raise ValueError("Integrity check failed: " + "; ".join(errors))

        return result

    def backup(self, target_directory: str | Path) -> Path:
        """
        Create a durable, consistent copy of the database files in target_directory.
        """
        self._ensure_open()
        if self._active_transaction is not None:
            raise RuntimeError("Cannot perform backup during an active transaction")

        target_path = Path(target_directory)
        if target_path.resolve() == self.path.resolve():
            raise ValueError(
                "Target backup directory cannot be the same as the database directory"
            )

        target_path.mkdir(parents=True, exist_ok=True)

        # Flush & sync all active data to disk
        self.segment.sync()
        self.page_manager.sync()
        self.wal.file.flush()
        if hasattr(self.wal.file, "fileno"):
            os.fsync(self.wal.file.fileno())

        # Copy data files to backup location (omit lock file)
        for filename in ("objectdb.data", "segment-0.dat", "objectdb.wal"):
            src_file = self.path / filename
            dst_file = target_path / filename
            if src_file.exists():
                shutil.copy2(src_file, dst_file)

        return target_path

    def close(self):
        if not self.is_open:
            return

        canonical_path = str(self.path.resolve())
        _ACTIVE_INSTANCES.pop(canonical_path, None)

        try:
            if self._active_transaction is not None:
                try:
                    self._active_transaction.rollback()
                except Exception:
                    pass
                self._active_transaction = None
            if self.segment is not None:
                self.segment.close()
        finally:
            try:
                if self.page_manager is not None:
                    self.page_manager.close()
            finally:
                try:
                    if self.wal is not None:
                        self.wal.close()
                finally:
                    if self._lock_fp is not None:
                        try:
                            fcntl.flock(self._lock_fp.fileno(), fcntl.LOCK_UN)
                            self._lock_fp.close()
                        except Exception:
                            pass
                        self._lock_fp = None
                    self.is_open = False
                    self.is_poisoned = False

    def simulate_crash(self):
        """Simulate an ungraceful crash by closing handles without rollback or commit."""
        if not self.is_open:
            return
        self.is_open = False
        self._active_transaction = None
        canonical_path = str(self.path.resolve())
        _ACTIVE_INSTANCES.pop(canonical_path, None)
        if self.segment is not None:
            try:
                self.segment.close()
            except Exception:
                pass
        if self.page_manager is not None:
            try:
                self.page_manager.close()
            except Exception:
                pass
        if self.wal is not None:
            try:
                self.wal.close()
            except Exception:
                pass
        if self._lock_fp is not None:
            try:
                fcntl.flock(self._lock_fp.fileno(), fcntl.LOCK_UN)
                self._lock_fp.close()
            except Exception:
                pass
            self._lock_fp = None

    def _ensure_open(self):
        if not self.is_open:
            raise RuntimeError("Database is not open")
        if self.is_poisoned:
            raise RuntimeError(
                "Database is in a poisoned state due to a previous commit failure; reopen to recover"
            )


class _DatabaseTransactionContext:
    """Context manager for explicit ObjectDB transactions."""

    def __init__(self, db):
        self.db = db
        self.tx = None
        self.old_adapter = None

    def __enter__(self):
        self.db._ensure_open()
        if self.db._active_transaction is not None:
            raise RuntimeError("Nested transactions are not supported")

        from objectdb.transaction import TransactionPageAdapter

        self.tx = self.db._begin_transaction()
        adapter = TransactionPageAdapter(self.tx, self.db.page_manager, self.db.tree)
        self.old_adapter = self.db.tree.page_manager
        self.db.tree.page_manager = adapter
        self.db._active_transaction = self.tx
        return self.tx

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is not None:
                if self.tx.state == TransactionState.ACTIVE:
                    self.tx.rollback()
                    header = read_header(self.db.page_manager.read_page(0))
                    self.db.tree.root_page_id = header["root_page_id"]
            else:
                self.db.segment.sync()
                self.tx.commit()
        except Exception:
            if self.tx.state in (TransactionState.COMMITTING, TransactionState.FAILED):
                self.db.is_poisoned = True
            raise
        finally:
            self.db.tree.page_manager = self.old_adapter
            self.db._active_transaction = None