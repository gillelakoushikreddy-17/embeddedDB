
from pathlib import Path

from objectdb.segment import Segment
from objectdb.record import encode_record, decode_record
from objectdb.persistent_btree import PersistentBTree


class Storage:
    def __init__(self, page_manager, segment_path: Path):
        self.tree = PersistentBTree(page_manager)
        self.segment = Segment(segment_path)

    def open(self):
        self.segment.open()

        try:
            self.tree.load_existing_tree()
        except ValueError:
            self.tree.create_empty_tree()

    def put(self, key: int, value: bytes):
        offset, size = self.segment.append(value)

        record = encode_record(
            0,       # segment ID: only one segment for now
            offset,
            size,
        )

        self.tree.insert_into_root(key, record)

    def get(self, key: int):
        record = self.tree.search(key)

        if record is None:
            return None

        segment_id, offset, size = decode_record(record)

        if segment_id != 0:
            raise ValueError("Unknown segment ID")

        return self.segment.read(offset, size)

    def close(self):
        self.segment.close()
