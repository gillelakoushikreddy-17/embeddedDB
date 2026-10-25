
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
        self.is_open = False

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

        try:
            wal.open()

            # Recover incomplete transactions before loading the tree.
            page_writer = TransactionalPageWriter(manager, wal)
            recovery = RecoveryManager(wal)
            recovery.recover(page_writer)

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
            manager.close()
            wal.close()
            raise

        self.page_manager = manager
        self.tree = tree
        self.segment = segment
        self.wal = wal
        self.is_open = True

    def put(self, key: int, value: bytes):
        self._ensure_open()

        if not isinstance(key, int) or isinstance(key, bool):
            raise TypeError("Key must be an integer")

        if not isinstance(value, bytes):
            raise TypeError("Value must be bytes")

        offset, size = self.segment.append(value)
        record = encode_record(0, offset, size)
        self.tree.insert_into_root(key, record)

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