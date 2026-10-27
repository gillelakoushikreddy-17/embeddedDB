"""
Scalability and Boundary Tests for Multi-Tree Persistent B+ Trees in ObjectDB.

Tests cover:
1. Large-scale tree creation (1,000 trees across numerous catalog pages).
2. Tree isolation at scale with identical keys across hundreds of trees.
3. Cold reopen and random retrieval across 1,000 trees.
4. Bulk tree creation rollback in explicit transactions.
5. Multi-tree transactions modifying dozens of trees in a single atomic commit.
6. Interleaved deletion and recreation of trees across multiple catalog pages.
7. Verification that dropped trees do not expose stale data upon recreation.
8. Full database integrity check across large multi-tree databases.
"""

import pytest
import random
from pathlib import Path

from objectdb import ObjectDB, TreeNotFoundError, TreeAlreadyExistsError


def test_scale_1000_trees_persistence_and_isolation(tmp_path):
    """
    Verify creating 1,000 distinct named trees across multiple linked catalog pages,
    verifying strict isolation and persistent recovery across database restarts.
    """
    db_path = tmp_path / "scale_1000.db"
    num_trees = 1000

    # 1. Create 1000 trees in batches
    with ObjectDB(db_path) as db:
        batch_size = 200
        for b in range(0, num_trees, batch_size):
            with db.transaction():
                for i in range(b, min(b + batch_size, num_trees)):
                    t = db.create_tree(f"tenant_{i:05d}")
                    # Write key 42 with unique tenant-specific data
                    t.put(42, f"data_tenant_{i}".encode("utf-8"))

        assert len(db.list_trees()) == num_trees
        catalog_pages = db.catalog.get_catalog_pages()
        assert len(catalog_pages) >= 5  # ~170 entries/page -> at least 5-6 pages

    # 2. Reopen and verify isolation and persistence
    with ObjectDB(db_path) as db:
        assert len(db.list_trees()) == num_trees

        # Sample 50 random trees and check contents
        rng = random.Random(42)
        sample = rng.sample(range(num_trees), 50)
        for idx in sample:
            t = db.open_tree(f"tenant_{idx:05d}")
            assert t.get(42) == f"data_tenant_{idx}".encode("utf-8")
            assert t.get(99) is None

        # Verify integrity
        integ = db.check_integrity(raise_on_error=True)
        assert integ["valid"] is True


def test_bulk_tree_creation_rollback(tmp_path):
    """
    Verify that creating many trees inside an aborted transaction completely rolls
    back the catalog without leaving phantom tree metadata or catalog corruptions.
    """
    db_path = tmp_path / "bulk_rollback.db"

    with ObjectDB(db_path) as db:
        # Establish initial trees
        with db.transaction():
            db.create_tree("tree_init_1").put(1, b"v1")
            db.create_tree("tree_init_2").put(2, b"v2")

        initial_trees = db.list_trees()
        assert initial_trees == ["tree_init_1", "tree_init_2"]

        # Attempt to create 150 trees and force rollback
        try:
            with db.transaction():
                for i in range(150):
                    t = db.create_tree(f"abort_tree_{i:03d}")
                    t.put(100, b"uncommitted")
                raise RuntimeError("Abort transaction!")
        except RuntimeError:
            pass

        # Verify catalog reverted completely
        assert db.list_trees() == ["tree_init_1", "tree_init_2"]
        for i in range(150):
            assert not db.catalog.has_tree(f"abort_tree_{i:03d}")
            with pytest.raises(TreeNotFoundError):
                db.open_tree(f"abort_tree_{i:03d}")

        t1 = db.open_tree("tree_init_1")
        assert t1.get(1) == b"v1"

    # Verify after restart
    with ObjectDB(db_path) as db:
        assert db.list_trees() == ["tree_init_1", "tree_init_2"]
        integ = db.check_integrity(raise_on_error=True)
        assert integ["valid"] is True


def test_interleaved_deletion_and_recreation_at_scale(tmp_path):
    """
    Create hundreds of trees across multiple catalog pages, delete half of them,
    verify that remaining trees are unharmed and deleted trees raise TreeNotFoundError.
    Then recreate a subset and verify clean isolation.
    """
    db_path = tmp_path / "interleaved_delete.db"
    total_trees = 400

    with ObjectDB(db_path) as db:
        with db.transaction():
            for i in range(total_trees):
                t = db.create_tree(f"stream_{i:04d}")
                t.put(1, f"original_{i}".encode("utf-8"))

        # Delete odd-numbered trees
        with db.transaction():
            for i in range(1, total_trees, 2):
                assert db.drop_tree(f"stream_{i:04d}") is True

        remaining = db.list_trees()
        assert len(remaining) == 200

        # Verify even-numbered trees still exist with their data
        for i in range(0, total_trees, 2):
            t = db.open_tree(f"stream_{i:04d}")
            assert t.get(1) == f"original_{i}".encode("utf-8")

        # Verify odd-numbered trees are gone
        for i in range(1, total_trees, 2):
            with pytest.raises(TreeNotFoundError):
                db.open_tree(f"stream_{i:04d}")

        # Recreate a few odd-numbered trees with new data
        with db.transaction():
            for i in [1, 3, 5]:
                t = db.create_tree(f"stream_{i:04d}")
                t.put(1, b"recreated_data")

        for i in [1, 3, 5]:
            t = db.open_tree(f"stream_{i:04d}")
            assert t.get(1) == b"recreated_data"

    # Reopen and check persistence
    with ObjectDB(db_path) as db:
        assert db.open_tree("stream_0000").get(1) == b"original_0"
        assert db.open_tree("stream_0001").get(1) == b"recreated_data"
        with pytest.raises(TreeNotFoundError):
            db.open_tree("stream_0007")

        integ = db.check_integrity(raise_on_error=True)
        assert integ["valid"] is True


def test_multi_tree_transaction_atomic_commit(tmp_path):
    """
    Verify that an explicit transaction mutating 50 distinct trees commits atomically.
    """
    db_path = tmp_path / "multi_tree_tx.db"
    num_trees = 50

    with ObjectDB(db_path) as db:
        for i in range(num_trees):
            db.create_tree(f"account_{i:03d}")

        # Atomically mutate all 50 trees in a single transaction
        with db.transaction():
            for i in range(num_trees):
                t = db.open_tree(f"account_{i:03d}")
                t.put(100, f"balance_{i * 10}".encode("utf-8"))

        for i in range(num_trees):
            t = db.open_tree(f"account_{i:03d}")
            assert t.get(100) == f"balance_{i * 10}".encode("utf-8")

    # Reopen and check
    with ObjectDB(db_path) as db:
        for i in range(num_trees):
            t = db.open_tree(f"account_{i:03d}")
            assert t.get(100) == f"balance_{i * 10}".encode("utf-8")
        integ = db.check_integrity(raise_on_error=True)
        assert integ["valid"] is True
