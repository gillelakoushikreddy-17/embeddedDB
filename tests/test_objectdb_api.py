import pytest
from objectdb import ObjectDB


def test_database_lifecycle_rejects_invalid_states(tmp_path):
    db_path = tmp_path / "test_lifecycle"
    db = ObjectDB(db_path)

    # Operations when not open
    with pytest.raises(RuntimeError, match="Database is not open"):
        db.get(1)

    with pytest.raises(RuntimeError, match="Database is not open"):
        db.put(1, b"val")

    with pytest.raises(RuntimeError, match="Database is not open"):
        db.delete(1)

    # Open database
    db.open()

    # Double open
    with pytest.raises(RuntimeError, match="Database is already open"):
        db.open()

    db.close()

    # Operations after close
    with pytest.raises(RuntimeError, match="Database is not open"):
        db.get(1)


def test_put_get_and_missing_keys(tmp_path):
    db_path = tmp_path / "test_api"
    with ObjectDB(db_path) as db:
        assert db.get(100) is None
        assert db.get(-5) is None
        assert db.get(0) is None

        db.put(100, b"hundred")
        assert db.get(100) == b"hundred"

        # Update existing key
        db.put(100, b"new_hundred")
        assert db.get(100) == b"new_hundred"

        assert db.get(99) is None


def test_delete_api(tmp_path):
    db_path = tmp_path / "test_delete"
    with ObjectDB(db_path) as db:
        db.put(1, b"one")
        db.put(2, b"two")
        db.put(3, b"three")

        assert db.get(2) == b"two"

        # Delete existing key
        assert db.delete(2) is True
        assert db.get(2) is None

        # Delete non-existent key
        assert db.delete(2) is False
        assert db.delete(999) is False

        # Remaining keys intact
        assert db.get(1) == b"one"
        assert db.get(3) == b"three"

    # Reopen to verify deletion persisted
    with ObjectDB(db_path) as db:
        assert db.get(1) == b"one"
        assert db.get(2) is None
        assert db.get(3) == b"three"


def test_type_validation(tmp_path):
    db_path = tmp_path / "test_types"
    with ObjectDB(db_path) as db:
        # Invalid keys
        for invalid_key in ["1", 1.5, True, False, None, [1], b"1"]:
            with pytest.raises(TypeError, match="Key must be an integer"):
                db.put(invalid_key, b"value")

            with pytest.raises(TypeError, match="Key must be an integer"):
                db.get(invalid_key)

            with pytest.raises(TypeError, match="Key must be an integer"):
                db.delete(invalid_key)

        # Invalid values
        for invalid_val in ["string", 123, True, None, [b"data"]]:
            with pytest.raises(TypeError, match="Value must be bytes"):
                db.put(1, invalid_val)


def test_large_dataset_splits_and_persists_across_reopen(tmp_path):
    db_path = tmp_path / "test_large"
    num_records = 300  # Will cause both leaf splits and internal node splits

    with ObjectDB(db_path) as db:
        for i in range(num_records):
            db.put(i, f"payload_{i}".encode("utf-8"))

        for i in range(num_records):
            assert db.get(i) == f"payload_{i}".encode("utf-8")

    # Reopen
    with ObjectDB(db_path) as db:
        for i in range(num_records):
            assert db.get(i) == f"payload_{i}".encode("utf-8")

        # Delete half of them
        for i in range(0, num_records, 2):
            assert db.delete(i) is True

        for i in range(num_records):
            if i % 2 == 0:
                assert db.get(i) is None
            else:
                assert db.get(i) == f"payload_{i}".encode("utf-8")

    # Reopen again to check deletion persistence
    with ObjectDB(db_path) as db:
        for i in range(num_records):
            if i % 2 == 0:
                assert db.get(i) is None
            else:
                assert db.get(i) == f"payload_{i}".encode("utf-8")
