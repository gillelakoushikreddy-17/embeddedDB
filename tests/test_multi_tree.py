"""
Comprehensive test suite for Multi-Tree Persistent B+ Tree support in ObjectDB.
Tests cover:
A. Tree creation (single, multiple, many scaling trees, duplicate and invalid names)
B. Tree isolation (identical keys, independent updates and deletions)
C. Persistence across database close and reopen
D. B+ tree behavior (leaf splits, internal node splits, missing keys, deletions)
E. Shared page management (distinct roots, non-overlapping pages, allocation tracking)
F. WAL and recovery (commits, rollbacks, crash recovery, interrupted tree creation)
G. Regression and backward compatibility (single-tree API alongside named trees, backup, integrity)
"""

import pytest
from pathlib import Path

from objectdb import ObjectDB, Tree, TreeAlreadyExistsError, TreeNotFoundError


# =====================================================================
# A. Tree Creation Tests
# =====================================================================

def test_tree_creation_single_and_three(tmp_path):
    with ObjectDB(tmp_path / "db") as db:
        assert db.list_trees() == []

        users = db.create_tree("users")
        assert isinstance(users, Tree)
        assert users.name == "users"
        assert db.list_trees() == ["users"]

        buckets = db.create_tree("buckets")
        objects = db.create_tree("objects")

        assert db.list_trees() == ["buckets", "objects", "users"]
        assert users.root_page_id != buckets.root_page_id
        assert buckets.root_page_id != objects.root_page_id
        assert users.root_page_id != objects.root_page_id


def test_tree_creation_duplicate_name_rejected(tmp_path):
    with ObjectDB(tmp_path / "db") as db:
        db.create_tree("users")

        with pytest.raises(TreeAlreadyExistsError, match="already exists"):
            db.create_tree("users")

        # TreeAlreadyExistsError is also a ValueError for backward compatibility
        with pytest.raises(ValueError):
            db.create_tree("users")


def test_tree_creation_invalid_names_rejected(tmp_path):
    with ObjectDB(tmp_path / "db") as db:
        # Empty string
        with pytest.raises(ValueError, match="empty"):
            db.create_tree("")

        # Non-string types
        with pytest.raises(TypeError):
            db.create_tree(None)

        with pytest.raises(TypeError):
            db.create_tree(12345)

        with pytest.raises(TypeError):
            db.create_tree(True)

        with pytest.raises(TypeError):
            db.create_tree(b"bytes_name")

        # Name exceeding maximum length (255 bytes)
        long_name = "a" * 256
        with pytest.raises(ValueError, match="maximum length"):
            db.create_tree(long_name)


def test_open_nonexistent_tree_rejected(tmp_path):
    with ObjectDB(tmp_path / "db") as db:
        with pytest.raises(TreeNotFoundError, match="does not exist"):
            db.open_tree("nonexistent")

        # TreeNotFoundError is also a KeyError
        with pytest.raises(KeyError):
            db.open_tree("nonexistent")


def test_tree_creation_many_trees_scaling(tmp_path):
    """
    Test creating many trees to verify that the catalog dynamically scales across
    multiple linked catalog pages without any hard-coded limits.
    """
    db_path = tmp_path / "db"
    tree_count = 200

    with ObjectDB(db_path) as db:
        for i in range(tree_count):
            name = f"tenant_tree_{i:04d}"
            t = db.create_tree(name)
            t.put(i, f"payload_{i}".encode("utf-8"))

        assert len(db.list_trees()) == tree_count

        # Verify catalog chain has allocated multiple catalog pages
        cat_pages = db.catalog.get_catalog_pages()
        assert len(cat_pages) > 1, f"Expected multiple catalog pages, got {len(cat_pages)}"

        report = db.check_integrity(raise_on_error=True)
        assert report["valid"] is True

    # Reopen and verify all 200 trees exist and can read their values
    with ObjectDB(db_path) as db:
        assert len(db.list_trees()) == tree_count
        for i in range(tree_count):
            name = f"tenant_tree_{i:04d}"
            t = db.open_tree(name)
            assert t.get(i) == f"payload_{i}".encode("utf-8")

        report = db.check_integrity(raise_on_error=True)
        assert report["valid"] is True


# =====================================================================
# B. Isolation Tests
# =====================================================================

def test_tree_key_value_isolation(tmp_path):
    """Verify that multiple trees have completely independent key spaces."""
    with ObjectDB(tmp_path / "db") as db:
        users = db.create_tree("users")
        buckets = db.create_tree("buckets")
        objects = db.create_tree("objects")

        # Insert identical keys into different trees
        users.put(100, b"alice_user")
        buckets.put(100, b"alice_bucket")
        objects.put(100, b"alice_object")

        assert users.get(100) == b"alice_user"
        assert buckets.get(100) == b"alice_bucket"
        assert objects.get(100) == b"alice_object"

        # Update a key in one tree without affecting the others
        users.put(100, b"alice_user_updated")
        assert users.get(100) == b"alice_user_updated"
        assert buckets.get(100) == b"alice_bucket"
        assert objects.get(100) == b"alice_object"

        # Delete a key in one tree without affecting the others
        assert users.delete(100) is True
        assert users.get(100) is None
        assert buckets.get(100) == b"alice_bucket"
        assert objects.get(100) == b"alice_object"


# =====================================================================
# C. Persistence Tests
# =====================================================================

def test_tree_persistence_across_reopen(tmp_path):
    db_path = tmp_path / "db"

    with ObjectDB(db_path) as db:
        users = db.create_tree("users")
        buckets = db.create_tree("buckets")

        users.put(1, b"user_1")
        users.put(2, b"user_2")
        buckets.put(10, b"bucket_10")
        buckets.put(20, b"bucket_20")

    # Reopen
    with ObjectDB(db_path) as db:
        assert sorted(db.list_trees()) == ["buckets", "users"]

        users = db.open_tree("users")
        buckets = db.open_tree("buckets")

        assert users.get(1) == b"user_1"
        assert users.get(2) == b"user_2"
        assert users.get(3) is None

        assert buckets.get(10) == b"bucket_10"
        assert buckets.get(20) == b"bucket_20"
        assert buckets.get(30) is None


# =====================================================================
# D. B+ Tree Behavior & Splitting Tests
# =====================================================================

def test_multi_tree_leaf_and_internal_splits(tmp_path):
    """
    Insert enough keys into multiple trees to cause leaf and internal splits.
    Verify structural validity and persistence of the new roots.
    """
    db_path = tmp_path / "db"
    key_count = 150  # Enough to trigger multiple splits in each tree

    with ObjectDB(db_path) as db:
        tree_a = db.create_tree("tree_a")
        tree_b = db.create_tree("tree_b")

        initial_root_a = tree_a.root_page_id
        initial_root_b = tree_b.root_page_id

        for k in range(key_count):
            tree_a.put(k, f"val_a_{k}".encode("utf-8"))
            tree_b.put(k * 2, f"val_b_{k * 2}".encode("utf-8"))

        # Verify that roots split and updated
        assert tree_a.root_page_id != initial_root_a
        assert tree_b.root_page_id != initial_root_b
        assert tree_a.root_page_id != tree_b.root_page_id

        # Verify all records
        for k in range(key_count):
            assert tree_a.get(k) == f"val_a_{k}".encode("utf-8")
            assert tree_b.get(k * 2) == f"val_b_{k * 2}".encode("utf-8")

        report = db.check_integrity(raise_on_error=True)
        assert report["valid"] is True

    # Reopen and verify after split persistence
    with ObjectDB(db_path) as db:
        tree_a = db.open_tree("tree_a")
        tree_b = db.open_tree("tree_b")

        for k in range(key_count):
            assert tree_a.get(k) == f"val_a_{k}".encode("utf-8")
            assert tree_b.get(k * 2) == f"val_b_{k * 2}".encode("utf-8")

        report = db.check_integrity(raise_on_error=True)
        assert report["valid"] is True


def test_multi_tree_missing_key_and_deletions(tmp_path):
    with ObjectDB(tmp_path / "db") as db:
        t = db.create_tree("test_tree")

        # Missing key returns None
        assert t.get(999) is None

        # Delete non-existent key returns False
        assert t.delete(999) is False

        # Insert, verify, delete, verify
        t.put(50, b"data_50")
        assert t.get(50) == b"data_50"
        assert t.delete(50) is True
        assert t.get(50) is None
        assert t.delete(50) is False


# =====================================================================
# E. Shared Page Management Tests
# =====================================================================

def test_shared_page_management(tmp_path):
    with ObjectDB(tmp_path / "db") as db:
        t1 = db.create_tree("t1")
        t2 = db.create_tree("t2")
        t3 = db.create_tree("t3")

        roots = {t1.root_page_id, t2.root_page_id, t3.root_page_id}
        assert len(roots) == 3, "Each tree must have a unique root page ID"

        cat_pages = set(db.catalog.get_catalog_pages())
        assert roots.isdisjoint(cat_pages), "Tree roots must not overlap with catalog pages"

        # Interleave allocations
        t1.put(1, b"a")
        t2.put(1, b"b")
        t3.put(1, b"c")

        report = db.check_integrity(raise_on_error=True)
        assert report["valid"] is True


# =====================================================================
# F. WAL, Transactions, and Recovery Tests
# =====================================================================

def test_multi_tree_explicit_transaction_commit(tmp_path):
    db_path = tmp_path / "db"

    with ObjectDB(db_path) as db:
        u = db.create_tree("users")
        b = db.create_tree("buckets")

        with db.transaction():
            u.put(1, b"alice")
            b.put(10, b"alice-bucket")

    # Reopen and check
    with ObjectDB(db_path) as db:
        u = db.open_tree("users")
        b = db.open_tree("buckets")
        assert u.get(1) == b"alice"
        assert b.get(10) == b"alice-bucket"


def test_multi_tree_explicit_transaction_rollback(tmp_path):
    db_path = tmp_path / "db"

    with ObjectDB(db_path) as db:
        u = db.create_tree("users")
        b = db.create_tree("buckets")

        u.put(1, b"committed_alice")
        b.put(10, b"committed_bucket")

        try:
            with db.transaction():
                u.put(1, b"uncommitted_alice")
                u.put(2, b"new_user")
                b.put(20, b"new_bucket")
                raise RuntimeError("Abort transaction")
        except RuntimeError:
            pass

        # In-memory check after rollback
        assert u.get(1) == b"committed_alice"
        assert u.get(2) is None
        assert b.get(20) is None

    # Persistent check after reopen
    with ObjectDB(db_path) as db:
        u = db.open_tree("users")
        b = db.open_tree("buckets")
        assert u.get(1) == b"committed_alice"
        assert u.get(2) is None
        assert b.get(20) is None


def test_interrupted_tree_creation_rollback(tmp_path):
    """
    Verify that creating a tree inside a transaction that rolls back
    completely reverts the catalog and reclaims any allocated pages.
    """
    db_path = tmp_path / "db"

    with ObjectDB(db_path) as db:
        db.create_tree("stable_tree")
        initial_trees = db.list_trees()

        try:
            with db.transaction():
                t = db.create_tree("aborted_tree")
                t.put(1, b"temp")
                raise RuntimeError("Abort tree creation")
        except RuntimeError:
            pass

        assert db.list_trees() == initial_trees
        assert not db.catalog.has_tree("aborted_tree")

        with pytest.raises(TreeNotFoundError):
            db.open_tree("aborted_tree")

        report = db.check_integrity(raise_on_error=True)
        assert report["valid"] is True

    # Reopen and verify
    with ObjectDB(db_path) as db:
        assert db.list_trees() == ["stable_tree"]
        with pytest.raises(TreeNotFoundError):
            db.open_tree("aborted_tree")


def test_multi_tree_crash_recovery(tmp_path):
    """Test simulated ungraceful crash with uncommitted multi-tree writes."""
    db_path = tmp_path / "db"

    db = ObjectDB(db_path)
    db.open()
    u = db.create_tree("users")
    u.put(1, b"committed_user")

    # Enter explicit transaction and write uncommitted mutation
    ctx = db.transaction()
    ctx.__enter__()
    u.put(2, b"uncommitted_user")

    # Simulate crash before commit (without calling ctx.__exit__)
    db.simulate_crash()

    # Reopen should trigger startup recovery and undo uncommitted user 2
    recovered_db = ObjectDB(db_path)
    recovered_db.open()
    try:
        u = recovered_db.open_tree("users")
        assert u.get(1) == b"committed_user"
        assert u.get(2) is None
        report = recovered_db.check_integrity(raise_on_error=True)
        assert report["valid"] is True
    finally:
        recovered_db.close()


def test_interrupted_tree_creation_crash_recovery(tmp_path):
    """Test crash recovery when process crashes while creating a tree in a transaction."""
    db_path = tmp_path / "db"

    db = ObjectDB(db_path)
    db.open()
    db.create_tree("permanent_tree")

    # Start explicit transaction, create a tree, and simulate crash without commit
    ctx = db.transaction()
    ctx.__enter__()
    t = db.create_tree("crash_tree")
    t.put(1, b"uncommitted")
    db.simulate_crash()

    # Reopen: crash_tree must not exist
    recovered_db = ObjectDB(db_path)
    recovered_db.open()
    try:
        assert recovered_db.list_trees() == ["permanent_tree"]
        with pytest.raises(TreeNotFoundError):
            recovered_db.open_tree("crash_tree")
        report = recovered_db.check_integrity(raise_on_error=True)
        assert report["valid"] is True
    finally:
        recovered_db.close()


def test_interrupted_tree_split_crash_recovery(tmp_path):
    """Test crash recovery when a tree root split is interrupted by a crash."""
    db_path = tmp_path / "db"

    db = ObjectDB(db_path)
    db.open()
    t = db.create_tree("split_tree")
    # Insert enough keys to be close to splitting
    for i in range(10):
        t.put(i, f"committed_{i}".encode("utf-8"))

    orig_root = t.root_page_id

    # Start transaction, insert enough keys to cause a split
    ctx = db.transaction()
    ctx.__enter__()
    for i in range(10, 200):
        t.put(i, f"uncommitted_{i}".encode("utf-8"))

    assert t.root_page_id != orig_root  # Split occurred

    # Simulate crash before commit
    db.simulate_crash()

    # Reopen: should recover to orig_root and keep only committed keys
    recovered_db = ObjectDB(db_path)
    recovered_db.open()
    try:
        t_recovered = recovered_db.open_tree("split_tree")
        assert t_recovered.root_page_id == orig_root
        for i in range(10):
            assert t_recovered.get(i) == f"committed_{i}".encode("utf-8")
        for i in range(10, 200):
            assert t_recovered.get(i) is None
        report = recovered_db.check_integrity(raise_on_error=True)
        assert report["valid"] is True
    finally:
        recovered_db.close()


# =====================================================================
# G. Regression & Backward Compatibility Tests
# =====================================================================

def test_backward_compatibility_single_tree_alongside_named_trees(tmp_path):
    """
    Verify that the original single-tree ObjectDB API (db.put, db.get, db.delete)
    continues to operate normally alongside new named trees.
    """
    db_path = tmp_path / "db"

    with ObjectDB(db_path) as db:
        # Single-tree API
        db.put(1, b"legacy_root_1")
        db.put(2, b"legacy_root_2")

        # Named tree API
        users = db.create_tree("users")
        users.put(1, b"user_1")

        # Verify values in both
        assert db.get(1) == b"legacy_root_1"
        assert db.get(2) == b"legacy_root_2"
        assert users.get(1) == b"user_1"

        # Delete from single tree
        assert db.delete(1) is True
        assert db.get(1) is None
        assert users.get(1) == b"user_1"

        report = db.check_integrity(raise_on_error=True)
        assert report["valid"] is True

    # Reopen and check
    with ObjectDB(db_path) as db:
        assert db.get(1) is None
        assert db.get(2) == b"legacy_root_2"
        users = db.open_tree("users")
        assert users.get(1) == b"user_1"


def test_multi_tree_backup_and_integrity(tmp_path):
    """Verify that db.backup() makes a full, consistent backup of all trees."""
    db_path = tmp_path / "db"
    backup_path = tmp_path / "backup"

    with ObjectDB(db_path) as db:
        u = db.create_tree("users")
        b = db.create_tree("buckets")

        u.put(1, b"user_data")
        b.put(100, b"bucket_data")
        db.put(999, b"default_data")

        # Create backup
        db.backup(backup_path)

    # Open backup and verify all trees and records
    with ObjectDB(backup_path) as backup_db:
        assert sorted(backup_db.list_trees()) == ["buckets", "users"]
        u = backup_db.open_tree("users")
        b = backup_db.open_tree("buckets")

        assert u.get(1) == b"user_data"
        assert b.get(100) == b"bucket_data"
        assert backup_db.get(999) == b"default_data"

        report = backup_db.check_integrity(raise_on_error=True)
        assert report["valid"] is True


def test_drop_tree(tmp_path):
    with ObjectDB(tmp_path / "db") as db:
        t = db.create_tree("temp_tree")
        t.put(1, b"value")

        assert "temp_tree" in db.list_trees()
        assert db.drop_tree("temp_tree") is True
        assert "temp_tree" not in db.list_trees()
        assert db.drop_tree("temp_tree") is False

        with pytest.raises(TreeNotFoundError):
            db.open_tree("temp_tree")

        report = db.check_integrity(raise_on_error=True)
        assert report["valid"] is True
