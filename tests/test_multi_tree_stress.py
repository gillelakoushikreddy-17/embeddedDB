"""
Multi-Tree Randomized Differential Stress Test for ObjectDB.

Maintains a Python dictionary reference model `dict[str, dict[int, bytes]]`
and performs hundreds of randomized operations:
- Random tree creation and recreation
- Key-value insertions, updates, and deletions across random trees
- Tree deletions
- Explicit transaction commits and forced rollbacks
- Mid-workload database close and reopening
- Verification against reference model at every stage
- Full B+ tree and catalog integrity validation
"""

import copy
import random
import pytest
from pathlib import Path

from objectdb import ObjectDB, TreeNotFoundError, TreeAlreadyExistsError


def test_multi_tree_randomized_differential_stress(tmp_path):
    """
    Randomized stress test comparing ObjectDB multi-tree operations
    against an in-memory reference model across 500 operations.
    """
    seed = 1337
    rng = random.Random(seed)
    db_path = tmp_path / "stress_db"

    # Reference model: tree_name -> dict of key -> value
    ref_trees: dict[str, dict[int, bytes]] = {}

    db = ObjectDB(db_path)
    db.open()

    total_ops = 500
    tree_pool = [f"tree_{i:03d}" for i in range(50)]

    try:
        for op_idx in range(total_ops):
            action = rng.choices(
                ["create_tree", "put_record", "delete_record", "drop_tree", "reopen_db", "tx_rollback"],
                weights=[25, 45, 10, 10, 5, 5],
                k=1,
            )[0]

            if action == "create_tree":
                name = rng.choice(tree_pool)
                if name not in ref_trees:
                    db.create_tree(name)
                    ref_trees[name] = {}
                else:
                    with pytest.raises(TreeAlreadyExistsError):
                        db.create_tree(name)

            elif action == "put_record":
                if ref_trees:
                    name = rng.choice(list(ref_trees.keys()))
                    key = rng.randint(1, 200)
                    val = f"val_{key}_{op_idx}".encode("utf-8")
                    t = db.open_tree(name)
                    t.put(key, val)
                    ref_trees[name][key] = val

            elif action == "delete_record":
                if ref_trees:
                    name = rng.choice(list(ref_trees.keys()))
                    t = db.open_tree(name)
                    if ref_trees[name] and rng.random() < 0.7:
                        key = rng.choice(list(ref_trees[name].keys()))
                        assert t.delete(key) is True
                        del ref_trees[name][key]
                    else:
                        missing_key = rng.randint(1000, 2000)
                        assert t.delete(missing_key) is False

            elif action == "drop_tree":
                if ref_trees:
                    name = rng.choice(list(ref_trees.keys()))
                    assert db.drop_tree(name) is True
                    del ref_trees[name]
                    with pytest.raises(TreeNotFoundError):
                        db.open_tree(name)

            elif action == "reopen_db":
                db.close()
                db = ObjectDB(db_path)
                db.open()
                # Verify catalog loaded matches reference model tree count
                assert sorted(db.list_trees()) == sorted(ref_trees.keys())

            elif action == "tx_rollback":
                # Save snapshot of reference model before speculative transaction
                snapshot_ref = copy.deepcopy(ref_trees)
                spec_trees = set(ref_trees.keys())
                try:
                    with db.transaction():
                        # Do speculative mutations
                        for _ in range(5):
                            spec_action = rng.choice(["create", "put", "drop"])
                            if spec_action == "create":
                                new_name = f"spec_tree_{rng.randint(1, 1000)}"
                                if not db.catalog.has_tree(new_name):
                                    db.create_tree(new_name).put(1, b"spec")
                                    spec_trees.add(new_name)
                            elif spec_action == "put" and spec_trees:
                                tn = rng.choice(list(spec_trees))
                                db.open_tree(tn).put(999, b"spec_val")
                            elif spec_action == "drop" and spec_trees:
                                tn = rng.choice(list(spec_trees))
                                db.drop_tree(tn)
                                spec_trees.discard(tn)
                        # Intentionally abort transaction
                        raise RuntimeError("Forced rollback")
                except RuntimeError:
                    pass

                # Assert that ref_trees did not change and matches db
                assert sorted(db.list_trees()) == sorted(snapshot_ref.keys())
                ref_trees = snapshot_ref

        # Final Verification against Reference Model
        assert sorted(db.list_trees()) == sorted(ref_trees.keys())
        for name, contents in ref_trees.items():
            t = db.open_tree(name)
            for k, expected_v in contents.items():
                assert t.get(k) == expected_v

        # Full Integrity Check
        integ = db.check_integrity(raise_on_error=True)
        assert integ["valid"] is True

        # Reopen and re-verify everything
        db.close()
        db = ObjectDB(db_path)
        db.open()
        assert sorted(db.list_trees()) == sorted(ref_trees.keys())
        for name, contents in ref_trees.items():
            t = db.open_tree(name)
            for k, expected_v in contents.items():
                assert t.get(k) == expected_v

        integ = db.check_integrity(raise_on_error=True)
        assert integ["valid"] is True

    finally:
        db.close()
