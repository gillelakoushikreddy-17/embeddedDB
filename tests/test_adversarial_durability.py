import random
import pytest
from embeddeddb import EmbeddedDB
from embeddeddb.database import MIN_KEY, MAX_KEY


def test_64bit_integer_key_boundaries_and_rejection(tmp_path):
    """
    Verify full 64-bit signed integer range:
    MIN_KEY (-2^63), MAX_KEY (2^63 - 1), 0, negative and positive keys,
    and strict rejection of overflow and invalid types.
    """
    db_path = tmp_path / "key_boundaries_db"
    db = EmbeddedDB(db_path)
    db.open()

    try:
        # Boundary keys
        boundary_keys = [
            MIN_KEY,
            MIN_KEY + 1,
            -1000,
            -1,
            0,
            1,
            1000,
            MAX_KEY - 1,
            MAX_KEY,
        ]

        for k in boundary_keys:
            val = f"val_{k}".encode("utf-8")
            db.put(k, val)
            assert db.get(k) == val

        # Keys out of range must be rejected
        with pytest.raises(ValueError, match="out of range for 64-bit signed integer"):
            db.put(MAX_KEY + 1, b"overflow")

        with pytest.raises(ValueError, match="out of range for 64-bit signed integer"):
            db.put(MIN_KEY - 1, b"underflow")

        with pytest.raises(ValueError, match="out of range for 64-bit signed integer"):
            db.get(MAX_KEY + 1)

        with pytest.raises(ValueError, match="out of range for 64-bit signed integer"):
            db.delete(MIN_KEY - 1)

        # Invalid types must be rejected
        for bad_key in [True, False, "string", 3.14, None, [], {}]:
            with pytest.raises(TypeError, match="Key must be an integer"):
                db.put(bad_key, b"data")
            with pytest.raises(TypeError, match="Key must be an integer"):
                db.get(bad_key)
            with pytest.raises(TypeError, match="Key must be an integer"):
                db.delete(bad_key)

        # Persistence check
        db.close()
        db = EmbeddedDB(db_path)
        db.open()

        for k in boundary_keys:
            val = f"val_{k}".encode("utf-8")
            assert db.get(k) == val

        # Deletion of boundary keys
        db.delete(MIN_KEY)
        assert db.get(MIN_KEY) is None
        db.delete(MAX_KEY)
        assert db.get(MAX_KEY) is None

        assert db.check_integrity()["valid"] is True
    finally:
        db.close()


def test_randomized_differential_testing_against_dict(tmp_path):
    """
    Stress-test EmbeddedDB against a reference Python dict model
    with randomized put, get, delete, commit, and rollback operations.
    """
    db_path = tmp_path / "differential_db"
    db = EmbeddedDB(db_path)
    db.open()

    reference_model = {}
    rng = random.Random(42)  # Deterministic seed

    key_space = list(range(-50, 50))

    try:
        # Prepopulate with 20 keys
        for k in key_space[:20]:
            val = f"init_{k}".encode()
            db.put(k, val)
            reference_model[k] = val

        for op_idx in range(300):
            op = rng.choice(["put", "get", "delete", "transaction_commit", "transaction_rollback"])

            if op == "put":
                k = rng.choice(key_space)
                val = f"val_{op_idx}_{k}".encode()
                db.put(k, val)
                reference_model[k] = val

            elif op == "get":
                k = rng.choice(key_space)
                expected = reference_model.get(k)
                actual = db.get(k)
                assert actual == expected, f"Mismatch on key {k}: expected {expected}, got {actual}"

            elif op == "delete":
                k = rng.choice(key_space)
                expected_deleted = k in reference_model
                actual_deleted = db.delete(k)
                assert actual_deleted == expected_deleted
                reference_model.pop(k, None)

            elif op == "transaction_commit":
                tx_changes = {}
                with db.transaction():
                    for _ in range(5):
                        k = rng.choice(key_space)
                        sub_op = rng.choice(["put", "delete"])
                        if sub_op == "put":
                            val = f"tx_c_{op_idx}_{k}".encode()
                            db.put(k, val)
                            tx_changes[k] = val
                        else:
                            db.delete(k)
                            tx_changes[k] = None

                for k, v in tx_changes.items():
                    if v is None:
                        reference_model.pop(k, None)
                    else:
                        reference_model[k] = v

            elif op == "transaction_rollback":
                # Save checkpoint of reference model
                try:
                    with db.transaction():
                        for _ in range(5):
                            k = rng.choice(key_space)
                            db.put(k, f"rollback_{op_idx}_{k}".encode())
                        # Force abort
                        raise ValueError("Simulated user abort")
                except ValueError:
                    pass

                # Model remains unchanged!
                # Verify that db matches reference_model
                check_key = rng.choice(key_space)
                assert db.get(check_key) == reference_model.get(check_key)

        # Full reconciliation at end
        for k in key_space:
            assert db.get(k) == reference_model.get(k)

        assert db.check_integrity()["valid"] is True
    finally:
        db.close()


def test_interrupted_tree_splits_and_crash_recovery(tmp_path):
    """
    Test that a crash occurring in the middle of multi-page internal node splits
    is cleanly rolled back, leaving the tree structure fully consistent and able
    to handle future insertions, splits, and deletions.
    """
    db_path = tmp_path / "split_crash_db"
    db = EmbeddedDB(db_path)
    db.open()

    try:
        # Pre-fill tree to induce near-split conditions
        for i in range(100):
            db.put(i * 10, f"base_{i}".encode() * 10)

        assert db.check_integrity()["valid"] is True
        db.close()

        # Reopen and start multi-key transaction that causes internal splits
        db = EmbeddedDB(db_path)
        db.open()

        try:
            with db.transaction():
                for i in range(100):
                    # Interleaved insertions causing multiple leaf and internal splits
                    db.put(i * 10 + 5, f"split_{i}".encode() * 15)
                # Simulate crash before commit
                db.simulate_crash()
                raise SystemExit("Crash mid-split")
        except SystemExit:
            pass

        # Database must recover cleanly on reopen
        db = EmbeddedDB(db_path)
        db.open()

        # Check integrity
        integrity = db.check_integrity()
        assert integrity["valid"] is True, f"Integrity failed after split crash: {integrity}"

        # Verify base keys are intact and split keys are not present
        for i in range(100):
            assert db.get(i * 10) == f"base_{i}".encode() * 10
            assert db.get(i * 10 + 5) is None

        # Verify tree can now accept new writes and perform normal splits
        for i in range(100):
            db.put(i * 10 + 5, f"committed_split_{i}".encode() * 15)

        for i in range(100):
            assert db.get(i * 10) == f"base_{i}".encode() * 10
            assert db.get(i * 10 + 5) == f"committed_split_{i}".encode() * 15

        assert db.check_integrity()["valid"] is True
    finally:
        db.close()


def test_concurrent_open_conflict_raises_error(tmp_path):
    """
    Verify that attempting to open a database when another instance
    already holds the lock in the same process raises RuntimeError.
    """
    db_path = tmp_path / "lock_conflict_db"
    db1 = EmbeddedDB(db_path)
    db1.open()

    try:
        db2 = EmbeddedDB(db_path)
        with pytest.raises(RuntimeError, match="already open"):
            db2.open()
    finally:
        db1.close()

    # After close, db2 can open
    db2 = EmbeddedDB(db_path)
    db2.open()
    db2.close()


def test_corrupted_mid_wal_record_prevents_database_open(tmp_path):
    """
    If a WAL record in the middle of the log is corrupted (e.g., bit flipped or invalid JSON),
    EmbeddedDB must fail closed on open() rather than silently skipping records or entering
    undefined state.
    """
    db_path = tmp_path / "corrupt_wal_db"
    db = EmbeddedDB(db_path)
    db.open()
    db.put(1, b"val1")
    db.put(2, b"val2")
    db.put(3, b"val3")
    db.close()

    wal_path = db_path / "embeddeddb.wal"
    with open(wal_path, "r+b") as f:
        lines = f.readlines()
        assert len(lines) >= 3
        # Corrupt a middle line by overwriting bytes with invalid JSON syntax
        middle_line = lines[1]
        corrupted_line = b'{"corrupted": !!' + middle_line[15:]
        lines[1] = corrupted_line
        f.seek(0)
        f.truncate()
        f.writelines(lines)
        f.flush()

    # Reopening MUST raise ValueError and refuse to open
    reopened = EmbeddedDB(db_path)
    with pytest.raises(ValueError, match="WAL contains an invalid record"):
        reopened.open()

    assert reopened.is_open is False

