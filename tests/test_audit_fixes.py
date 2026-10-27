import pytest
import tempfile
from objectdb import (
    ObjectDB,
    TreeAlreadyExistsError,
    TreeNotFoundError,
)
from objectdb.page_manager import PageManager, PAGE_SIZE
from objectdb.btree import (
    encode_leaf_node,
    decode_leaf_node,
    encode_internal_node,
    decode_internal_node,
)
from objectdb.record import encode_record


def test_named_tree_created_in_transaction_operates_cleanly_in_subsequent_transactions(tmp_path):
    """
    Bug 1A: If a tree is created inside an explicit transaction, its adapter must be
    cleanly restored to NamedTreePageAdapter upon commit so that subsequent transactions
    can mutate the tree without 'Transaction is already committed' errors.
    """
    db_path = tmp_path / "tree_in_tx_db"
    with ObjectDB(db_path) as db:
        with db.transaction():
            t = db.create_tree("tree1")
            t.put(1, b"v1")

        # Mutate inside a second transaction
        with db.transaction():
            t.put(2, b"v2")

        assert t.get(1) == b"v1"
        assert t.get(2) == b"v2"

    # Verify persistence
    with ObjectDB(db_path) as db:
        t = db.open_tree("tree1")
        assert t.get(1) == b"v1"
        assert t.get(2) == b"v2"


def test_named_tree_dropped_in_aborted_transaction_is_fully_functional(tmp_path):
    """
    Bug 1B: If a tree is dropped inside an explicit transaction that rolls back,
    the tree must be restored in the catalog, its in-memory registry maintained,
    and its adapter reset so that it can continue to be mutated inside future transactions.
    """
    db_path = tmp_path / "dropped_rollback_db"
    with ObjectDB(db_path) as db:
        u = db.create_tree("users")
        u.put(1, b"one")

        # Aborted drop
        try:
            with db.transaction():
                db.drop_tree("users")
                raise RuntimeError("Force rollback of drop")
        except RuntimeError:
            pass

        assert db.catalog.has_tree("users")
        assert "users" in db.list_trees()

        # Mutate in a new transaction
        with db.transaction():
            u.put(2, b"two")

        assert u.get(1) == b"one"
        assert u.get(2) == b"two"

    # Persistence verification
    with ObjectDB(db_path) as db:
        u = db.open_tree("users")
        assert u.get(1) == b"one"
        assert u.get(2) == b"two"


def test_pagemanager_closed_file_operations_raise_runtime_error(tmp_path):
    """
    Bug 2: Calling read_page, write_page, allocate_page, update_root_page_id,
    or update_catalog_page_id when PageManager is closed should raise
    RuntimeError('Database file is not open'), not AttributeError.
    """
    db_file = tmp_path / "closed.db"
    pm = PageManager(db_file)

    with pytest.raises(RuntimeError, match="not open"):
        pm.read_page(1)

    with pytest.raises(RuntimeError, match="not open"):
        pm.write_page(1, b"\x00" * PAGE_SIZE)

    with pytest.raises(RuntimeError, match="not open"):
        pm.allocate_page()

    with pytest.raises(RuntimeError, match="not open"):
        pm.update_root_page_id(1)

    with pytest.raises(RuntimeError, match="not open"):
        pm.update_catalog_page_id(1)


def test_leaf_and_internal_node_encoding_decoding_rejects_duplicates():
    """
    Bug 4: B+ tree leaf and internal nodes must reject duplicate and out-of-order keys.
    """
    r1 = encode_record(0, 0, 10)
    r2 = encode_record(0, 10, 10)

    # Leaf duplicate keys
    with pytest.raises(ValueError, match="sorted"):
        encode_leaf_node([10, 10], [r1, r2])

    with pytest.raises(ValueError, match="sorted"):
        encode_leaf_node([20, 10], [r1, r2])

    # Internal duplicate keys
    with pytest.raises(ValueError, match="sorted"):
        encode_internal_node([10, 10], [1, 2, 3])

    with pytest.raises(ValueError, match="sorted"):
        encode_internal_node([30, 20], [1, 2, 3])


def test_named_tree_created_in_transaction_operates_outside_transaction(tmp_path):
    """
    Verify that a tree created in an explicit transaction can be mutated
    outside of transactions once committed.
    """
    db_path = tmp_path / "tx_outside_db"
    with ObjectDB(db_path) as db:
        with db.transaction():
            t = db.create_tree("tree1")
            t.put(1, b"v1")

        # Mutate outside transaction
        t.put(2, b"v2")
        assert t.get(1) == b"v1"
        assert t.get(2) == b"v2"
        assert t.delete(1) is True
        assert t.get(1) is None


def test_named_tree_dropped_in_committed_transaction_raises_tree_not_found(tmp_path):
    """
    Verify that a tree dropped in a committed transaction raises TreeNotFoundError
    on subsequent operations rather than leaking dead transaction handles.
    """
    db_path = tmp_path / "tx_drop_commit_db"
    with ObjectDB(db_path) as db:
        t = db.create_tree("tree1")
        t.put(1, b"v1")

        with db.transaction():
            assert db.drop_tree("tree1") is True

        assert "tree1" not in db.list_trees()
        assert not db.catalog.has_tree("tree1")

        with pytest.raises(TreeNotFoundError):
            t.put(2, b"v2")

        with pytest.raises(TreeNotFoundError):
            t.get(1)

        with pytest.raises(TreeNotFoundError):
            t.delete(1)


def test_drop_and_recreate_tree_in_same_transaction(tmp_path):
    """
    Verify that dropping a tree and creating a new tree with the same name
    in the same transaction commits cleanly.
    """
    db_path = tmp_path / "tx_drop_recreate_db"
    with ObjectDB(db_path) as db:
        t1 = db.create_tree("mytree")
        t1.put(10, b"old_data")

        with db.transaction():
            db.drop_tree("mytree")
            t2 = db.create_tree("mytree")
            t2.put(20, b"new_data")

        assert t2.get(10) is None
        assert t2.get(20) == b"new_data"

    with ObjectDB(db_path) as db:
        t = db.open_tree("mytree")
        assert t.get(10) is None
        assert t.get(20) == b"new_data"


def test_decode_leaf_and_internal_node_rejects_malformed_order():
    """
    Verify that decoding a page with duplicate or descending keys raises ValueError.
    """
    import struct
    from objectdb.btree import (
        LEAF_NODE_TYPE,
        LEAF_HEADER_FORMAT,
        INTERNAL_NODE_TYPE,
        INTERNAL_HEADER_FORMAT,
        KEY_FORMAT,
        PAGE_ID_FORMAT,
    )

    # Craft leaf page with duplicate keys [5, 5]
    header = struct.pack(LEAF_HEADER_FORMAT, LEAF_NODE_TYPE, 2)
    k1 = struct.pack("<q", 5)
    r1 = encode_record(0, 0, 10)
    k2 = struct.pack("<q", 5)
    r2 = encode_record(0, 10, 10)
    leaf_bytes = header + k1 + r1 + k2 + r2
    leaf_bytes = leaf_bytes.ljust(PAGE_SIZE, b"\x00")

    with pytest.raises(ValueError, match="sorted"):
        decode_leaf_node(leaf_bytes)

    # Craft internal page with duplicate keys [10, 10]
    int_header = struct.pack(INTERNAL_HEADER_FORMAT, INTERNAL_NODE_TYPE, 2)
    int_keys = struct.pack("<qq", 10, 10)
    int_children = struct.pack("<III", 1, 2, 3)
    int_bytes = int_header + int_keys + int_children
    int_bytes = int_bytes.ljust(PAGE_SIZE, b"\x00")

    with pytest.raises(ValueError, match="sorted"):
        decode_internal_node(int_bytes)

