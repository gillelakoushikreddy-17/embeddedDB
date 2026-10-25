
from pathlib import Path

from objectdb.page_manager import PageManager
from objectdb.persistent_btree import PersistentBTree
from objectdb.segment import Segment
from objectdb.record import encode_record, decode_record
from objectdb.wal import WriteAheadLog
from objectdb.transactional_page_writer import TransactionalPageWriter
from objectdb.recovery import RecoveryManager

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
        self._active_transaction = None

    def __enter__(self):
        if not self.is_open:
            self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def open(self):
        if self.is_open:
            raise RuntimeError("Database is already open")

        self.path.mkdir(parents=True, exist_ok=True)

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
                from objectdb.header import read_header, NO_ROOT_PAGE

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
            raise

        self.page_manager = manager
        self.tree = tree
        self.segment = segment
        self.wal = wal
        self.page_writer = page_writer
        self.is_open = True

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

        if not isinstance(key, int) or isinstance(key, bool):
            raise TypeError("Key must be an integer")

        if not isinstance(value, bytes):
            raise TypeError("Value must be bytes")

        if self._active_transaction is not None:
            offset, size = self.segment.append(value)
            record = encode_record(0, offset, size)
            self.tree.insert_into_root(key, record)
            return

        from objectdb.transaction import TransactionPageAdapter

        tx = self._begin_transaction()
        adapter = TransactionPageAdapter(tx, self.page_manager, self.tree)
        old_adapter = self.tree.page_manager
        self.tree.page_manager = adapter

        try:
            offset, size = self.segment.append(value)
            record = encode_record(0, offset, size)
            self.tree.insert_into_root(key, record)
            tx.commit()
            self.segment.sync()
        except Exception:
            try:
                tx.rollback()
            finally:
                from objectdb.header import read_header
                header = read_header(self.page_manager.read_page(0))
                self.tree.root_page_id = header["root_page_id"]
            raise
        finally:
            self.tree.page_manager = old_adapter

    def delete(self, key: int) -> bool:
        self._ensure_open()

        if not isinstance(key, int) or isinstance(key, bool):
            raise TypeError("Key must be an integer")

        if self._active_transaction is not None:
            return self.tree.delete(key)

        from objectdb.transaction import TransactionPageAdapter

        tx = self._begin_transaction()
        adapter = TransactionPageAdapter(tx, self.page_manager, self.tree)
        old_adapter = self.tree.page_manager
        self.tree.page_manager = adapter

        try:
            deleted = self.tree.delete(key)
            tx.commit()
            return deleted
        except Exception:
            try:
                tx.rollback()
            finally:
                from objectdb.header import read_header
                header = read_header(self.page_manager.read_page(0))
                self.tree.root_page_id = header["root_page_id"]
            raise
        finally:
            self.tree.page_manager = old_adapter

    def get(self, key: int):
        self._ensure_open()

        if not isinstance(key, int) or isinstance(key, bool):
            raise TypeError("Key must be an integer")

        record = self.tree.search(key)

        if record is None:
            return None

        segment_id, offset, size = decode_record(record)

        if segment_id != 0:
            raise ValueError("Unknown segment ID")

        return self.segment.read(offset, size)

    def close(self):
        if not self.is_open:
            return

        try:
            if self._active_transaction is not None:
                try:
                    self._active_transaction.rollback()
                except Exception:
                    pass
                self._active_transaction = None
            self.segment.close()
        finally:
            try:
                self.page_manager.close()
            finally:
                self.wal.close()
                self.is_open = False

    def _ensure_open(self):
        if not self.is_open:
            raise RuntimeError("Database is not open")


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
                self.tx.rollback()
                from objectdb.header import read_header
                header = read_header(self.db.page_manager.read_page(0))
                self.db.tree.root_page_id = header["root_page_id"]
            else:
                self.tx.commit()
                self.db.segment.sync()
        finally:
            self.db.tree.page_manager = self.old_adapter
            self.db._active_transaction = None