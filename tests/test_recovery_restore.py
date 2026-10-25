import base64

from objectdb.recovery import RecoveryManager


class FakeWAL:
    def __init__(self, records):
        self.records = records

    def read_all(self):
        return self.records


class FakePageWriter:
    def __init__(self):
        self.restored = []
        self.synced = False

    def restore_page(self, page_id, original_data):
        self.restored.append((page_id, original_data))

    def sync(self):
        self.synced = True


def make_record(lsn, record_type, transaction_id, **extra):
    data = {"transaction_id": transaction_id}
    data.update(extra)

    return {
        "lsn": lsn,
        "type": record_type,
        "data": data,
    }


def test_recovery_restores_incomplete_transaction():
    records = [
        make_record(1, "BEGIN", 1),
        make_record(
            2,
            "PAGE_BEFORE_IMAGE",
            1,
            page_id=2,
            original_data=base64.b64encode(b"\x00" * 4096).decode("ascii"),
        ),
    ]

    writer = FakePageWriter()
    manager = RecoveryManager(FakeWAL(records))

    result = manager.recover(writer)

    assert writer.restored
    assert writer.synced
    assert result["recovered_transactions"] == [1]
    assert result["restored_pages"] == 1


def test_recovery_does_not_undo_committed_transaction():
    records = [
        make_record(1, "BEGIN", 1),
        make_record(
            2,
            "PAGE_BEFORE_IMAGE",
            1,
            page_id=2,
            original_data=base64.b64encode(b"\x00" * 4096).decode("ascii"),
        ),

        make_record(3, "COMMIT", 1),
    ]

    writer = FakePageWriter()
    manager = RecoveryManager(FakeWAL(records))

    result = manager.recover(writer)

    assert writer.restored == []
    assert writer.synced is False
    assert result["recovered_transactions"] == []
    assert result["restored_pages"] == 0


def test_recovery_restores_repeated_page_writes_in_reverse_order():
    records = [
        make_record(1, "BEGIN", 1),
        make_record(
            2,
            "PAGE_BEFORE_IMAGE",
            1,
            page_id=2,
            original_data=base64.b64encode(b"A" * 4096).decode("ascii"),
        ),
        make_record(
            3,
            "PAGE_BEFORE_IMAGE",
            1,
            page_id=2,
            original_data=base64.b64encode(b"B" * 4096).decode("ascii"),
        ),
    ]

    writer = FakePageWriter()
    manager = RecoveryManager(FakeWAL(records))

    manager.recover(writer)
    print("ACTUAL:", [(page_id, value[:20]) for page_id, value in writer.restored])

    assert writer.restored == [
        (2, base64.b64encode(b"B" * 4096).decode("ascii")),
        (2, base64.b64encode(b"A" * 4096).decode("ascii")),
    ]