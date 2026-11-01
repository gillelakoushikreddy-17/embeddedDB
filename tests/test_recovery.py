from embeddeddb.recovery import RecoveryManager
import pytest
import base64

def valid_before_image():
    return base64.b64encode(b"\x00" * 4096).decode("ascii")


class FakeWAL:
    """Provide WAL records for recovery tests."""

    def __init__(self, records):
        self.records = records

    def read_all(self):
        return self.records


def make_record(record_type, transaction_id, **extra):
    data = {"transaction_id": transaction_id}
    data.update(extra)

    return {
        "lsn": 1,
        "type": record_type,
        "data": data,
    }


def test_identifies_committed_and_incomplete_transactions():
    records = [
        make_record("BEGIN", 1),
        make_record(
            "PAGE_BEFORE_IMAGE",
            1,
            page_id=2,
            original_data=valid_before_image(),
        ),
        make_record("COMMIT", 1),
        make_record("BEGIN", 2),
        make_record(
            "PAGE_BEFORE_IMAGE",
            2,
            page_id=3,
            original_data=valid_before_image(),
        ),
    ]

    manager = RecoveryManager(FakeWAL(records))
    result = manager.analyze()

    assert result["committed"] == [1]
    assert result["incomplete"] == [2]
    assert result["rolled_back"] == []


def test_identifies_transaction_with_no_page_changes():
    records = [
        make_record("BEGIN", 1),
        make_record("COMMIT", 1),
    ]

    manager = RecoveryManager(FakeWAL(records))

    result = manager.analyze()

    assert result["committed"] == [1]
    assert result["incomplete"] == []


def test_classifies_rolled_back_transaction_separately():
    records = [
        make_record("BEGIN", 1),
        make_record(
            "PAGE_BEFORE_IMAGE",
            1,
            page_id=2,
            original_data=valid_before_image(),
        ),
        make_record("ROLLBACK", 1),
    ]

    manager = RecoveryManager(FakeWAL(records))

    result = manager.analyze()

    assert result["committed"] == []
    assert result["rolled_back"] == [1]
    assert result["incomplete"] == []


def test_preserves_before_images_in_log_order():
    first_image = base64.b64encode(b"\x01" * 4096).decode("ascii")
    second_image = base64.b64encode(b"\x02" * 4096).decode("ascii")

    records = [
        make_record("BEGIN", 1),
        make_record(
            "PAGE_BEFORE_IMAGE",
            1,
            page_id=2,
            original_data=first_image,
        ),
        make_record(
            "PAGE_BEFORE_IMAGE",
            1,
            page_id=2,
            original_data=second_image,
        ),
    ]

    manager = RecoveryManager(FakeWAL(records))
    result = manager.analyze()

    before_images = result["transactions"][1]["before_images"]

    assert len(before_images) == 2
    assert before_images[0]["data"]["original_data"] == first_image
    assert before_images[1]["data"]["original_data"] == second_image


def test_rejects_invalid_transaction_id():
    records = [
        make_record("BEGIN", True),
    ]

    manager = RecoveryManager(FakeWAL(records))

    try:
        manager.analyze()
    except ValueError as exc:
        assert "transaction ID" in str(exc)
    else:
        raise AssertionError("Expected ValueError")



def test_analyze_rejects_unknown_record_type():
    class FakeWAL:
        def read_all(self):
            return [
                {
                    "lsn": 1,
                    "type": "UNKNOWN_OPERATION",
                    "data": {"transaction_id": 1},
                }
            ]

    recovery = RecoveryManager(FakeWAL())


    with pytest.raises(ValueError, match="Unknown WAL record type"):

        recovery.analyze()



def test_analyze_rejects_commit_without_begin():
    class FakeWAL:
        def read_all(self):
            return [
                {
                    "lsn": 1,
                    "type": "COMMIT",
                    "data": {"transaction_id": 1},
                }
            ]

    recovery = RecoveryManager(FakeWAL())

    with pytest.raises(
        ValueError,
        match="COMMIT without BEGIN",
    ):
        recovery.analyze()



def test_analyze_rejects_duplicate_begin():
    class FakeWAL:
        def read_all(self):
            return [
                {
                    "lsn": 1,
                    "type": "BEGIN",
                    "data": {"transaction_id": 1},
                },
                {
                    "lsn": 2,
                    "type": "BEGIN",
                    "data": {"transaction_id": 1},
                },
            ]

    recovery = RecoveryManager(FakeWAL())

    with pytest.raises(
        ValueError,
        match="Duplicate BEGIN",
    ):
        recovery.analyze()


def test_analyze_rejects_commit_after_rollback():
    class FakeWAL:
        def read_all(self):
            return [
                {
                    "lsn": 1,
                    "type": "BEGIN",
                    "data": {"transaction_id": 1},
                },
                {
                    "lsn": 2,
                    "type": "ROLLBACK",
                    "data": {"transaction_id": 1},
                },
                {
                    "lsn": 3,
                    "type": "COMMIT",
                    "data": {"transaction_id": 1},
                },
            ]

    recovery = RecoveryManager(FakeWAL())

    with pytest.raises(
        ValueError,
        match="COMMIT after ROLLBACK",
    ):
        recovery.analyze()


def test_analyze_rejects_rollback_without_begin():
    class FakeWAL:
        def read_all(self):
            return [
                {
                    "lsn": 1,
                    "type": "ROLLBACK",
                    "data": {"transaction_id": 1},
                },
            ]

    recovery = RecoveryManager(FakeWAL())

    with pytest.raises(
        ValueError,
        match="ROLLBACK without BEGIN",
    ):
        recovery.analyze()



def test_analyze_rejects_page_before_image_without_begin():
    class FakeWAL:
        def read_all(self):
            return [
                {
                    "lsn": 1,
                    "type": "PAGE_BEFORE_IMAGE",
                    "data": {
                        "transaction_id": 1,
                        "page_id": 1,
                        "original_data": "AAAA",
                    },
                },
            ]

    recovery = RecoveryManager(FakeWAL())

    with pytest.raises(
        ValueError,
        match="PAGE_BEFORE_IMAGE without BEGIN",
    ):
        recovery.analyze()



def test_analyze_rejects_reused_transaction_id():
    class FakeWAL:
        def read_all(self):
            return [
                {
                    "lsn": 1,
                    "type": "BEGIN",
                    "data": {"transaction_id": 1},
                },
                {
                    "lsn": 2,
                    "type": "COMMIT",
                    "data": {"transaction_id": 1},
                },
                {
                    "lsn": 3,
                    "type": "BEGIN",
                    "data": {"transaction_id": 1},
                },
            ]

    recovery = RecoveryManager(FakeWAL())

    with pytest.raises(
        ValueError,
        match="Transaction ID reused after completion",
    ):
        recovery.analyze()










def test_recover_does_not_restore_rolled_back_transaction():
    records = [
        make_record("BEGIN", 1),
        make_record(
            "PAGE_BEFORE_IMAGE",
            1,
            page_id=2,
            original_data=valid_before_image(),
        ),
        make_record("ROLLBACK", 1),
    ]

    class FakePageWriter:
        def __init__(self):
            self.restored_pages = []
            self.sync_called = False

        def restore_page(self, page_id, original_data):
            self.restored_pages.append((page_id, original_data))

        def sync(self):
            self.sync_called = True

    wal = FakeWAL(records)
    recovery = RecoveryManager(wal)
    writer = FakePageWriter()

    result = recovery.recover(writer)

    assert result["recovered_transactions"] == []
    assert result["restored_pages"] == 0
    assert writer.restored_pages == []
    assert writer.sync_called is False


def test_analyze_rejects_invalid_before_image_page_id():
    records = [
        make_record("BEGIN", 1),
        make_record(
            "PAGE_BEFORE_IMAGE",
            1,
            page_id=True,
            original_data="AAAA",
        ),
    ]

    recovery = RecoveryManager(FakeWAL(records))

    with pytest.raises(
        ValueError,
        match="Invalid page ID in before-image",
    ):

        recovery.analyze()

def test_analyze_rejects_invalid_base64_before_image():
    records = [
        make_record("BEGIN", 1),
        make_record(
            "PAGE_BEFORE_IMAGE",
            1,
            page_id=1,
            original_data="not-valid-base64!!!",
        ),
    ]

    recovery = RecoveryManager(FakeWAL(records))

    with pytest.raises(ValueError, match="Invalid Base64 before-image"):
        recovery.analyze()


def test_analyze_rejects_wrong_before_image_size():
    import base64

    records = [
        make_record("BEGIN", 1),
        make_record(
            "PAGE_BEFORE_IMAGE",
            1,
            page_id=1,
            original_data=base64.b64encode(b"too short").decode("ascii"),
        ),
    ]

    recovery = RecoveryManager(FakeWAL(records))

    with pytest.raises(
        ValueError,
        match="exactly 4096 bytes",
    ):
        recovery.analyze()


def test_analyze_rejects_root_before_image_without_begin():
    records = [
        make_record(
            "ROOT_PAGE_BEFORE_IMAGE",
            1,
            original_root_page_id=0,
            new_root_page_id=1,
        ),
    ]
    recovery = RecoveryManager(FakeWAL(records))
    with pytest.raises(ValueError, match="ROOT_PAGE_BEFORE_IMAGE without BEGIN"):
        recovery.analyze()


def test_analyze_rejects_root_before_image_after_commit():
    records = [
        make_record("BEGIN", 1),
        make_record("COMMIT", 1),
        make_record(
            "ROOT_PAGE_BEFORE_IMAGE",
            1,
            original_root_page_id=0,
            new_root_page_id=1,
        ),
    ]
    recovery = RecoveryManager(FakeWAL(records))
    with pytest.raises(ValueError, match="ROOT_PAGE_BEFORE_IMAGE after COMMIT"):
        recovery.analyze()


def test_analyze_rejects_invalid_root_page_ids():
    records = [
        make_record("BEGIN", 1),
        make_record(
            "ROOT_PAGE_BEFORE_IMAGE",
            1,
            original_root_page_id=-1,
            new_root_page_id=1,
        ),
    ]
    recovery = RecoveryManager(FakeWAL(records))
    with pytest.raises(ValueError, match="Invalid original root page ID"):
        recovery.analyze()


def test_recover_restores_root_page_id_for_incomplete_transaction():
    records = [
        make_record("BEGIN", 1),
        make_record(
            "ROOT_PAGE_BEFORE_IMAGE",
            1,
            original_root_page_id=1,
            new_root_page_id=5,
        ),
    ]

    class RootTrackingPageWriter:
        def __init__(self):
            self.restored_roots = []
            self.sync_called = False

        def restore_root_page_id(self, root_id):
            self.restored_roots.append(root_id)

        def sync(self):
            self.sync_called = True

    writer = RootTrackingPageWriter()
    recovery = RecoveryManager(FakeWAL(records))
    result = recovery.recover(writer)

    assert writer.restored_roots == [1]
    assert writer.sync_called is True
    assert result["recovered_transactions"] == [1]