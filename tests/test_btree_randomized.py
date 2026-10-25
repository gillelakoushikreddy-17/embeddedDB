import random
import pytest
from objectdb import ObjectDB


def test_randomized_btree_differential_testing(tmp_path):
    """
    Deterministic randomized differential test comparing ObjectDB against a
    Python dict reference model over 500+ mutations with periodic restarts and
    integrity audits.
    """
    rng = random.Random(42)
    db_path = tmp_path / "random_db"
    reference = {}

    db = ObjectDB(db_path)
    db.open()

    # Pre-insert special boundary keys
    special_keys = [
        -9223372036854775808,  # -2^63
        9223372036854775807,   # 2^63 - 1
        -1,
        0,
        1,
        -1000000,
        1000000,
    ]

    for key in special_keys:
        val = f"special_{key}".encode("ascii")
        db.put(key, val)
        reference[key] = val

    db.check_integrity(raise_on_error=True)

    # 600 randomized operations
    key_pool = list(special_keys)
    for op_idx in range(1, 601):
        op = rng.choice(["insert", "update", "delete", "get"])

        if op in ("insert", "update") or not key_pool:
            if rng.random() < 0.6 or not key_pool:
                # Generate new key
                key = rng.randint(-100000, 100000)
                if key not in key_pool:
                    key_pool.append(key)
            else:
                key = rng.choice(key_pool)

            val = f"val_{op_idx}_{key}".encode("ascii")
            db.put(key, val)
            reference[key] = val

        elif op == "delete":
            key = rng.choice(key_pool)
            deleted = db.delete(key)
            if key in reference:
                assert deleted is True
                del reference[key]
            else:
                assert deleted is False

        elif op == "get":
            key = rng.choice(key_pool) if key_pool else 0
            expected = reference.get(key)
            actual = db.get(key)
            assert actual == expected, f"Mismatch for key {key}: expected {expected}, got {actual}"

        # Periodic check and restart
        if op_idx % 150 == 0:
            db.check_integrity(raise_on_error=True)
            db.close()
            db = ObjectDB(db_path)
            db.open()

    # Final deep audit
    report = db.check_integrity(raise_on_error=True)
    assert report["valid"] is True
    assert report["total_keys"] == len(reference)

    # Validate every key in the reference dict
    for key, expected_val in reference.items():
        assert db.get(key) == expected_val, f"Key {key} value mismatch"

    # Validate absent keys
    for absent_key in [99999999, -99999999, 123456789]:
        if absent_key not in reference:
            assert db.get(absent_key) is None

    db.close()
