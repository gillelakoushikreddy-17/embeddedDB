
from objectdb.page_manager import PageManager
from objectdb.persistent_btree import PersistentBTree
from objectdb.btree import (
    LEAF_NODE_TYPE,
    INTERNAL_NODE_TYPE,
    decode_leaf_node,
    decode_internal_node,
)
import struct




def test_create_empty_tree(tmp_path):
    db_path = tmp_path / "tree.db"

    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        tree = PersistentBTree(manager)

        root_page_id = tree.create_empty_tree()

        assert root_page_id == 1
        assert tree.root_page_id == 1

        keys, records = tree.read_root()

        assert keys == []
        assert records == []

    finally:
        manager.close()



def test_load_existing_tree(tmp_path):
    db_path = tmp_path / "persistent.db"

    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        tree = PersistentBTree(manager)
        tree.create_empty_tree()

        # Simulate a new tree object after restarting the application.
        reopened_tree = PersistentBTree(manager)

        root_page_id = reopened_tree.load_existing_tree()

        assert root_page_id == 1
        assert reopened_tree.root_page_id == 1

        keys, records = reopened_tree.read_root()
        assert keys == []
        assert records == []

    finally:
        manager.close()



def test_root_survives_database_reopen(tmp_path):
    db_path = tmp_path / "restart.db"

    # First session: create and save the tree.
    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        tree = PersistentBTree(manager)
        tree.create_empty_tree()
    finally:
        manager.close()

    # Second session: reopen the same database file.
    manager = PageManager(db_path)
    manager.open()

    try:
        reopened_tree = PersistentBTree(manager)

        root_page_id = reopened_tree.load_existing_tree()

        assert root_page_id == 1

        keys, records = reopened_tree.read_root()
        assert keys == []
        assert records == []
    finally:
        manager.close()



from objectdb.record import encode_record


def test_insert_into_root(tmp_path):
    db_path = tmp_path / "insert.db"

    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        tree = PersistentBTree(manager)
        tree.create_empty_tree()

        record = encode_record(1, 100, 500)
        tree.insert_into_root(42, record)

        keys, records = tree.read_root()

        assert keys == [42]
        assert records == [record]
    finally:
        manager.close()



def test_insert_survives_database_reopen(tmp_path):
    db_path = tmp_path / "insert_restart.db"

    # Session 1: create the tree and insert a key.
    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        tree = PersistentBTree(manager)
        tree.create_empty_tree()

        record = encode_record(1, 100, 500)
        tree.insert_into_root(42, record)
    finally:
        manager.close()

    # Session 2: reopen the database and recover the tree.
    manager = PageManager(db_path)
    manager.open()

    try:
        reopened_tree = PersistentBTree(manager)
        reopened_tree.load_existing_tree()

        keys, records = reopened_tree.read_root()

        assert keys == [42]
        assert records == [encode_record(1, 100, 500)]
    finally:
        manager.close()



def test_search_key_in_root(tmp_path):
    db_path = tmp_path / "search.db"

    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        tree = PersistentBTree(manager)
        tree.create_empty_tree()

        record = encode_record(1, 100, 500)
        tree.insert_into_root(42, record)

        assert tree.search(42) == record
        assert tree.search(99) is None
    finally:
        manager.close()


def test_search_after_database_reopen(tmp_path):
    db_path = tmp_path / "search_restart.db"

    # Session 1: create and insert.
    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    record = encode_record(1, 100, 500)

    try:
        tree = PersistentBTree(manager)
        tree.create_empty_tree()
        tree.insert_into_root(42, record)
    finally:
        manager.close()

    # Session 2: reopen and search.
    manager = PageManager(db_path)
    manager.open()

    try:
        tree = PersistentBTree(manager)
        tree.load_existing_tree()

        assert tree.search(42) == record
        assert tree.search(99) is None
    finally:
        manager.close()


def test_multiple_keys_and_update(tmp_path):
    db_path = tmp_path / "multiple.db"

    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        tree = PersistentBTree(manager)
        tree.create_empty_tree()

        record_10 = encode_record(1, 100, 500)
        record_42 = encode_record(1, 200, 600)
        record_75 = encode_record(1, 300, 700)
        updated_record_42 = encode_record(1, 400, 800)

        tree.insert_into_root(42, record_42)
        tree.insert_into_root(10, record_10)
        tree.insert_into_root(75, record_75)

        assert tree.search(10) == record_10
        assert tree.search(42) == record_42
        assert tree.search(75) == record_75

        # Insert the same key with a new record reference.
        tree.insert_into_root(42, updated_record_42)

        assert tree.search(42) == updated_record_42

        keys, records = tree.read_root()
        assert keys == [10, 42, 75]
        assert len(records) == 3

    finally:
        manager.close()



def test_leaf_page_capacity(tmp_path):
    db_path = tmp_path / "capacity.db"

    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        tree = PersistentBTree(manager)
        tree.create_empty_tree()

        for key in range(146):
            record = encode_record(1, key * 100, 100)
            tree.insert_into_root(key, record)

        keys, records = tree.read_root()

        assert len(keys) == 146
        assert len(records) == 146
        assert keys == list(range(146))

    finally:
        manager.close()

def test_leaf_split_creates_internal_root(tmp_path):
    from objectdb.btree import (
        INTERNAL_NODE_TYPE,
        decode_internal_node,
    )
    import struct

    db_path = tmp_path / "split.db"

    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    tree = PersistentBTree(manager)
    tree.create_empty_tree()

    for key in range(1, 148):
        tree.insert_into_root(key, b"x" * 20)

    root_page = manager.read_page(tree.root_page_id)
    node_type, _ = struct.unpack_from("<BH", root_page, 0)

    assert node_type == INTERNAL_NODE_TYPE

    separator_keys, child_page_ids = decode_internal_node(root_page)

    assert separator_keys == [74]
    assert len(child_page_ids) == 2

    manager.close()



def test_search_after_leaf_split(tmp_path):
    db_path = tmp_path / "search_split.db"

    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    tree = PersistentBTree(manager)
    tree.create_empty_tree()

    for key in range(1, 148):
        tree.insert_into_root(key, b"x" * 20)

    assert tree.search(20) == b"x" * 20
    assert tree.search(100) == b"x" * 20
    assert tree.search(147) == b"x" * 20
    assert tree.search(200) is None

    manager.close()


def test_split_tree_survives_database_reopen(tmp_path):
    db_path = tmp_path / "persistent_split.db"

    # Open and populate the database.
    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    tree = PersistentBTree(manager)
    tree.create_empty_tree()

    for key in range(1, 148):
        tree.insert_into_root(key, b"x" * 20)

    manager.close()

    # Reopen the same database file.
    manager = PageManager(db_path)
    manager.open()

    tree = PersistentBTree(manager)
    tree.load_existing_tree()

    assert tree.search(20) == b"x" * 20
    assert tree.search(100) == b"x" * 20
    assert tree.search(147) == b"x" * 20
    assert tree.search(200) is None

    manager.close()


def test_find_leaf_page_after_split(tmp_path):
    db_path = tmp_path / "find_leaf.db"

    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    tree = PersistentBTree(manager)
    tree.create_empty_tree()

    for key in range(1, 148):
        tree.insert_into_root(key, b"x" * 20)

    left_page_id = tree._find_leaf_page(50)
    right_page_id = tree._find_leaf_page(100)

    assert left_page_id != right_page_id

    left_keys, _ = decode_leaf_node(
        manager.read_page(left_page_id)
    )
    right_keys, _ = decode_leaf_node(
        manager.read_page(right_page_id)
    )

    assert 50 in left_keys
    assert 100 in right_keys

    manager.close()



def test_find_parent_page_after_split(tmp_path):
    db_path = tmp_path / "test.db"

    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    tree = PersistentBTree(manager)
    tree.create_empty_tree()

    for key in range(1, 148):
        tree.insert_into_root(
            key,
            encode_record(1, key * 10, 100),
        )

    left_page_id = tree._find_leaf_page(50)
    right_page_id = tree._find_leaf_page(100)

    assert tree._find_parent_page(left_page_id) == tree.root_page_id
    assert tree._find_parent_page(right_page_id) == tree.root_page_id

    manager.close()




def test_find_leaf_page_with_nested_internal_nodes(tmp_path):
    from objectdb.page_manager import PageManager
    from objectdb.persistent_btree import PersistentBTree
    from objectdb.btree import (
        encode_leaf_node,
        encode_internal_node,
    )

    db_path = tmp_path / "nested_tree.db"

    pm = PageManager(db_path)
    pm.initialize()
    pm.open()  # Open the database before allocating pages

    tree = PersistentBTree(pm)

    tree = PersistentBTree(pm)

    # Create three leaf pages.
    leaf_10 = pm.allocate_page()
    leaf_30 = pm.allocate_page()
    leaf_60 = pm.allocate_page()

    import struct

    record = struct.pack("<IQQ", 1, 0, 100)

    pm.write_page(
        leaf_10,
        encode_leaf_node([10], [record]),
    )
    pm.write_page(
        leaf_30,
        encode_leaf_node([30, 40], [record, record]),
    )
    pm.write_page(
        leaf_60,
        encode_leaf_node([60, 70], [record, record]),
    )

    # Create a non-root internal node.
    internal_left = pm.allocate_page()
    pm.write_page(
        internal_left,
        encode_internal_node(
            [20],
            [leaf_10, leaf_30],
        ),
    )

    # Create the root with an internal child.
    root = pm.allocate_page()
    pm.write_page(
        root,
        encode_internal_node(
            [50],
            [internal_left, leaf_60],
        ),
    )

    pm.update_root_page_id(root)
    tree.root_page_id = root

    # Key 30 requires traversing two internal levels.
    assert tree._find_leaf_page(30) == leaf_30

    # Search should now work through the nested structure.
    assert tree.search(30) == record
    assert tree.search(35) is None
    assert tree._find_parent_page(leaf_10) == internal_left
    assert tree._find_parent_page(leaf_60) == root
    assert tree._find_parent_page(root) is None
    pm.close()


def test_split_internal_entries():
    from objectdb.persistent_btree import PersistentBTree

    tree = PersistentBTree(None)

    keys = [10, 20, 30, 40, 50]
    child_page_ids = [1, 2, 3, 4, 5, 6]

    (
        left_keys,
        left_children,
        promoted_key,
        right_keys,
        right_children,
    ) = tree._split_internal_entries(keys, child_page_ids)

    assert left_keys == [10, 20]
    assert left_children == [1, 2, 3]

    assert promoted_key == 30

    assert right_keys == [40, 50]
    assert right_children == [4, 5, 6]


def test_write_split_internal_nodes(tmp_path):
    from objectdb.page_manager import PageManager
    from objectdb.persistent_btree import PersistentBTree
    from objectdb.btree import decode_internal_node

    db_path = tmp_path / "internal_split.db"

    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        tree = PersistentBTree(manager)

        left_page_id, right_page_id = (
            tree._write_split_internal_nodes(
                [10, 20],
                [1, 2, 3],
                [40, 50],
                [4, 5, 6],
            )
        )

        left_page = manager.read_page(left_page_id)
        right_page = manager.read_page(right_page_id)

        assert decode_internal_node(left_page) == (
            [10, 20], [1, 2, 3]
        )
        assert decode_internal_node(right_page) == (
            [40, 50], [4, 5, 6]
        )

        assert left_page_id != right_page_id

    finally:
        manager.close()


def test_insert_separator_into_parent(tmp_path):
    from objectdb.page_manager import PageManager
    from objectdb.persistent_btree import PersistentBTree
    from objectdb.btree import (
        encode_internal_node,
        decode_internal_node,
    )

    db_path = tmp_path / "test.db"
    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        tree = PersistentBTree(manager)

        # Create a parent with one key and two children.
        parent_page_id = manager.allocate_page()
        parent_page = encode_internal_node(
            [30], [10, 11]
        )
        manager.write_page(parent_page_id, parent_page)

        # Replace child 10 with children 8 and 9,
        # inserting separator key 20.
        tree._insert_into_parent(
            parent_page_id=parent_page_id,
            separator_key=20,
            old_page_id=10,
            left_page_id=8,
            right_page_id=9,
        )

        # Read the updated parent.
        updated_page = manager.read_page(parent_page_id)
        keys, children = decode_internal_node(updated_page)

        assert keys == [20, 30]
        assert children == [8, 9, 11]
        assert len(children) == len(keys) + 1

    finally:
        manager.close()


def test_create_internal_root_after_split(tmp_path):
    from objectdb.page_manager import PageManager
    from objectdb.persistent_btree import PersistentBTree
    from objectdb.btree import decode_internal_node

    db_path = tmp_path / "test.db"
    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        tree = PersistentBTree(manager)

        # Create an initial leaf root.
        tree.create_empty_tree()
        old_root_id = tree.root_page_id

        # Create a new internal root pointing to two children.
        left_page_id = manager.allocate_page()
        right_page_id = manager.allocate_page()

        tree._create_internal_root(
            left_page_id=left_page_id,
            right_page_id=right_page_id,
            separator_key=50,
        )

        # Verify the new root was persisted.
        assert tree.root_page_id != old_root_id

        root_page = manager.read_page(tree.root_page_id)
        keys, children = decode_internal_node(root_page)

        assert keys == [50]
        assert children == [left_page_id, right_page_id]

    finally:
        manager.close()


def test_insert_into_parent_splits_root(tmp_path):
    from objectdb.page_manager import PageManager
    from objectdb.persistent_btree import PersistentBTree
    from objectdb.btree import (
        encode_internal_node,
        decode_internal_node,
    )

    db_path = tmp_path / "test.db"
    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        tree = PersistentBTree(manager)

        # Create an existing internal root.
        tree.create_empty_tree()
        old_root_id = manager.allocate_page()

        # Use a full-capacity root: 340 keys and 341 children.
        keys = list(range(1000, 1340))
        children = list(range(500, 841))

        manager.write_page(
            old_root_id,
            encode_internal_node(keys, children),
        )
        manager.update_root_page_id(old_root_id)
        tree.root_page_id = old_root_id

        # Insert one separator, causing the root to overflow.
        tree._insert_into_parent(
            parent_page_id=old_root_id,
            separator_key=1340,
            old_page_id=840,
            left_page_id=840,
            right_page_id=841,
        )

        # A new root should now replace the overflowing root.
        assert tree.root_page_id != old_root_id

        new_root = manager.read_page(tree.root_page_id)
        root_keys, root_children = decode_internal_node(new_root)

        assert len(root_keys) == 1
        assert len(root_children) == 2

        # Both children of the new root must be internal nodes.
        for child_id in root_children:
            child_page = manager.read_page(child_id)
            child_keys, child_children = decode_internal_node(child_page)
            assert len(child_children) == len(child_keys) + 1

    finally:
        manager.close()



def test_insert_into_parent_splits_non_root_parent(tmp_path):
    from objectdb.page_manager import PageManager
    from objectdb.persistent_btree import PersistentBTree
    from objectdb.btree import (
        encode_internal_node,
        decode_internal_node,
    )

    db_path = tmp_path / "test.db"
    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        tree = PersistentBTree(manager)

        # Create a root with two children.
        root_id = manager.allocate_page()
        left_parent_id = manager.allocate_page()
        right_parent_id = manager.allocate_page()

        manager.write_page(
            root_id,
            encode_internal_node([5000], [left_parent_id, right_parent_id]),
        )

        # Make the left parent full.
        keys = list(range(1000, 1340))
        children = list(range(100, 441))

        manager.write_page(
            left_parent_id,
            encode_internal_node(keys, children),
        )

        # Make the right parent a valid internal node.
        manager.write_page(
            right_parent_id,
            encode_internal_node([6000], [900, 901]),
        )

        manager.update_root_page_id(root_id)
        tree.root_page_id = root_id

        # Overflow the non-root parent.
        tree._insert_into_parent(
            parent_page_id=left_parent_id,
            separator_key=1340,
            old_page_id=440,
            left_page_id=440,
            right_page_id=441,
        )

        # The root should still exist, but now have three children.
        updated_root = manager.read_page(root_id)
        root_keys, root_children = decode_internal_node(updated_root)

        assert len(root_keys) == 2
        assert len(root_children) == 3

        # Both newly split children should be valid internal nodes.
        for child_id in root_children[:2]:
            child_page = manager.read_page(child_id)
            child_keys, child_children = decode_internal_node(child_page)
            assert len(child_children) == len(child_keys) + 1

    finally:
        manager.close()

def test_recursive_internal_split_creates_new_root(tmp_path):
    from objectdb.page_manager import PageManager
    from objectdb.persistent_btree import PersistentBTree
    from objectdb.btree import (
        encode_internal_node,
        decode_internal_node,
    )

    db_path = tmp_path / "test.db"
    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        tree = PersistentBTree(manager)

        # Create the root and its two internal children.
        root_id = manager.allocate_page()
        left_parent_id = manager.allocate_page()
        right_parent_id = manager.allocate_page()

        # A root with 340 keys is at maximum capacity.
        root_keys = list(range(10000, 10340))
        root_children = list(range(1000, 1341))

        manager.write_page(
            root_id,
            encode_internal_node(root_keys, root_children),
        )

        # Create the full left parent that will overflow.
        left_keys = list(range(1000, 1340))
        left_children = list(range(2000, 2341))

        manager.write_page(
            left_parent_id,
            encode_internal_node(left_keys, left_children),
        )

        manager.write_page(
            right_parent_id,
            encode_internal_node([20000], [3000, 3001]),
        )

        # Replace the root's first and last child references with
        # the actual internal children used in this test.
        root_children[0] = left_parent_id
        root_children[-1] = right_parent_id

        manager.write_page(
            root_id,
            encode_internal_node(root_keys, root_children),
        )

        manager.update_root_page_id(root_id)
        tree.root_page_id = root_id

        # Overflow the left parent. Its promoted separator is
        # inserted into the already-full root, causing another split.
        tree._insert_into_parent(
            parent_page_id=left_parent_id,
            separator_key=1340,
            old_page_id=2340,
            left_page_id=2340,
            right_page_id=2341,
        )

        # A new root should have been created.
        assert tree.root_page_id != root_id

        new_root_page = manager.read_page(tree.root_page_id)
        new_root_keys, new_root_children = decode_internal_node(
            new_root_page
        )

        assert len(new_root_keys) == 1
        assert len(new_root_children) == 2

        # Both children of the new root must be valid internal nodes.
        for child_id in new_root_children:
            child_page = manager.read_page(child_id)
            child_keys, child_children = decode_internal_node(child_page)

            assert len(child_children) == len(child_keys) + 1

    finally:
        manager.close()



def test_search_after_multiple_leaf_splits(tmp_path):
    from objectdb.page_manager import PageManager
    from objectdb.persistent_btree import PersistentBTree
    import struct
    db_path = tmp_path / "test.db"
    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        tree = PersistentBTree(manager)
        tree.create_empty_tree()

        # Insert enough keys to cause multiple leaf splits.
        for key in range(500):
            record = struct.pack("<IQQ", 1, key * 100, 10)
            tree.insert_into_root(key, record)
        # Verify keys from different parts of the tree.
        for key in [0, 1, 73, 74, 145, 146, 250, 400, 499]:
            assert tree.search(key) is not None

        # Verify keys that were never inserted are absent.
        for key in [-1, 500, 1000]:
            assert tree.search(key) is None

    finally:
        manager.close()

def test_persistence_after_multiple_leaf_splits(tmp_path):
    import struct
    from objectdb.page_manager import PageManager
    from objectdb.persistent_btree import PersistentBTree

    db_path = tmp_path / "test.db"

    # Phase 1: Insert records and close the database.
    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        tree = PersistentBTree(manager)
        tree.create_empty_tree()

        for key in range(500):
            record = struct.pack("<IQQ", 1, key * 100, 10)
            tree.insert_into_root(key, record)

    finally:
        manager.close()

    # Phase 2: Reopen the database and load the tree from disk.
    manager = PageManager(db_path)
    manager.open()

    try:
        tree = PersistentBTree(manager)
        tree.load_existing_tree()

        for key in [0, 1, 73, 74, 145, 146, 250, 400, 499]:
            assert tree.search(key) is not None

        for key in [-1, 500, 1000]:
            assert tree.search(key) is None

    finally:
        manager.close()

def test_update_existing_key_after_multiple_splits(tmp_path):
    import struct
    from objectdb.page_manager import PageManager
    from objectdb.persistent_btree import PersistentBTree
    from objectdb.btree import decode_leaf_node

    db_path = tmp_path / "test.db"
    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        tree = PersistentBTree(manager)
        tree.create_empty_tree()

        # Insert enough keys to trigger multiple leaf splits.
        for key in range(500):
            record = struct.pack("<IQQ", 1, key * 100, 10)
            tree.insert_into_root(key, record)

        # Update an existing key with a different record.
        updated_record = struct.pack("<IQQ", 2, 9999, 50)
        tree.insert_into_root(250, updated_record)

        # Search should return the updated record.
        assert tree.search(250) == updated_record

        # The key must appear exactly once in its leaf.
        leaf_page_id = tree._find_leaf_page(250)
        leaf_page = manager.read_page(leaf_page_id)
        keys, records = decode_leaf_node(leaf_page)

        assert keys.count(250) == 1
        assert records[keys.index(250)] == updated_record

    finally:
        manager.close()

def test_descending_order_insertion(tmp_path):
    import struct
    from objectdb.page_manager import PageManager
    from objectdb.persistent_btree import PersistentBTree

    db_path = tmp_path / "test.db"
    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        tree = PersistentBTree(manager)
        tree.create_empty_tree()

        # Insert keys from largest to smallest.
        for key in range(499, -1, -1):
            record = struct.pack("<IQQ", 1, key * 100, 10)
            tree.insert_into_root(key, record)

        # Every inserted key should remain searchable.
        for key in range(500):
            expected = struct.pack("<IQQ", 1, key * 100, 10)
            assert tree.search(key) == expected

        # Missing keys should not be found.
        assert tree.search(-1) is None
        assert tree.search(500) is None

    finally:
        manager.close()

def test_random_order_insertion(tmp_path):
    import random
    import struct
    from objectdb.page_manager import PageManager
    from objectdb.persistent_btree import PersistentBTree

    db_path = tmp_path / "test.db"
    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        tree = PersistentBTree(manager)
        tree.create_empty_tree()

        keys = list(range(500))
        random.Random(42).shuffle(keys)

        for key in keys:
            record = struct.pack("<IQQ", 1, key * 100, 10)
            tree.insert_into_root(key, record)

        for key in range(500):
            expected = struct.pack("<IQQ", 1, key * 100, 10)
            assert tree.search(key) == expected

    finally:
        manager.close()

def test_leaf_keys_remain_sorted_after_random_insertions(tmp_path):
    import random
    import struct
    from objectdb.page_manager import PageManager
    from objectdb.persistent_btree import PersistentBTree
    from objectdb.btree import decode_leaf_node, LEAF_NODE_TYPE

    db_path = tmp_path / "test.db"
    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        tree = PersistentBTree(manager)
        tree.create_empty_tree()

        keys_to_insert = list(range(500))
        random.Random(42).shuffle(keys_to_insert)

        for key in keys_to_insert:
            record = struct.pack("<IQQ", 1, key * 100, 10)
            tree.insert_into_root(key, record)

        # Inspect every allocated page.
        for page_id in range(1, db_path.stat().st_size // 4096):
            page = manager.read_page(page_id)

            if page[0] == LEAF_NODE_TYPE:
                keys, records = decode_leaf_node(page)
                assert keys == sorted(keys)
                assert len(keys) == len(records)

    finally:
        manager.close()


def test_no_duplicate_keys_across_leaf_pages(tmp_path):
    import random
    import struct

    from objectdb.page_manager import PageManager
    from objectdb.persistent_btree import PersistentBTree
    from objectdb.btree import (
        decode_leaf_node,
        decode_internal_node,
        LEAF_NODE_TYPE,
        INTERNAL_NODE_TYPE,
    )

    db_path = tmp_path / "test.db"
    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        tree = PersistentBTree(manager)
        tree.create_empty_tree()

        keys_to_insert = list(range(500))
        random.Random(42).shuffle(keys_to_insert)

        for key in keys_to_insert:
            record = struct.pack("<IQQ", 1, key * 100, 10)
            tree.insert_into_root(key, record)

        # Visit only pages reachable from the current root.
        all_leaf_keys = []
        pages_to_visit = [tree.root_page_id]
        visited_pages = set()

        while pages_to_visit:
            page_id = pages_to_visit.pop()

            assert page_id not in visited_pages, (
                f"Page {page_id} was visited more than once"
            )
            visited_pages.add(page_id)

            page = manager.read_page(page_id)
            node_type = page[0]

            if node_type == LEAF_NODE_TYPE:
                keys, records = decode_leaf_node(page)
                all_leaf_keys.extend(keys)

            elif node_type == INTERNAL_NODE_TYPE:
                _, child_page_ids = decode_internal_node(page)
                pages_to_visit.extend(child_page_ids)

            else:
                raise AssertionError(
                    f"Unexpected node type {node_type} on page {page_id}"
                )

        assert len(all_leaf_keys) == 500
        assert len(set(all_leaf_keys)) == 500
        assert set(all_leaf_keys) == set(range(500))

    finally:
        manager.close()

def test_internal_nodes_remain_valid_after_random_insertions(tmp_path):
    import random
    import struct

    from objectdb.page_manager import PageManager
    from objectdb.persistent_btree import PersistentBTree
    from objectdb.btree import (
        decode_internal_node,
        decode_leaf_node,
        INTERNAL_NODE_TYPE,
        LEAF_NODE_TYPE,
    )

    db_path = tmp_path / "test.db"
    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        tree = PersistentBTree(manager)
        tree.create_empty_tree()

        keys = list(range(500))
        random.Random(42).shuffle(keys)

        for key in keys:
            record = struct.pack("<IQQ", 1, key * 100, 10)
            tree.insert_into_root(key, record)

        pages_to_visit = [tree.root_page_id]
        visited_pages = set()

        while pages_to_visit:
            page_id = pages_to_visit.pop()

            assert page_id not in visited_pages
            visited_pages.add(page_id)

            page = manager.read_page(page_id)

            if page[0] == INTERNAL_NODE_TYPE:
                node_keys, children = decode_internal_node(page)

                assert node_keys == sorted(node_keys)
                assert len(children) == len(node_keys) + 1

                pages_to_visit.extend(children)

            elif page[0] == LEAF_NODE_TYPE:
                leaf_keys, records = decode_leaf_node(page)

                assert leaf_keys == sorted(leaf_keys)
                assert len(leaf_keys) == len(records)

            else:
                raise AssertionError(
                    f"Unexpected node type on page {page_id}"
                )

    finally:
        manager.close()

def test_child_key_ranges_after_random_insertions(tmp_path):
    import random
    import struct

    from objectdb.page_manager import PageManager
    from objectdb.persistent_btree import PersistentBTree
    from objectdb.btree import (
        decode_internal_node,
        decode_leaf_node,
        INTERNAL_NODE_TYPE,
        LEAF_NODE_TYPE,
    )

    db_path = tmp_path / "test.db"
    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        tree = PersistentBTree(manager)
        tree.create_empty_tree()

        keys = list(range(500))
        random.Random(42).shuffle(keys)

        for key in keys:
            record = struct.pack("<IQQ", 1, key * 100, 10)
            tree.insert_into_root(key, record)

        def check_subtree(page_id, lower=None, upper=None):
            page = manager.read_page(page_id)

            if page[0] == LEAF_NODE_TYPE:
                leaf_keys, _ = decode_leaf_node(page)

                for key in leaf_keys:
                    if lower is not None:
                        assert key >= lower
                    if upper is not None:
                        assert key < upper

                return leaf_keys

            assert page[0] == INTERNAL_NODE_TYPE

            separators, children = decode_internal_node(page)

            assert separators == sorted(separators)
            assert len(children) == len(separators) + 1

            collected_keys = []

            for index, child_id in enumerate(children):
                child_lower = (
                    lower if index == 0 else separators[index - 1]
                )
                child_upper = (
                    upper if index == len(separators) else separators[index]
                )

                collected_keys.extend(
                    check_subtree(child_id, child_lower, child_upper)
                )

            return collected_keys

        all_keys = check_subtree(tree.root_page_id)

        assert sorted(all_keys) == list(range(500))

    finally:
        manager.close()


def test_tree_structure_after_reopening(tmp_path):
    import random
    import struct

    from objectdb.page_manager import PageManager
    from objectdb.persistent_btree import PersistentBTree
    from objectdb.btree import (
        decode_internal_node,
        decode_leaf_node,
        INTERNAL_NODE_TYPE,
        LEAF_NODE_TYPE,
    )

    db_path = tmp_path / "test.db"

    # Insert keys and close the database.
    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    try:
        tree = PersistentBTree(manager)
        tree.create_empty_tree()

        keys = list(range(500))
        random.Random(42).shuffle(keys)

        for key in keys:
            record = struct.pack("<IQQ", 1, key * 100, 10)
            tree.insert_into_root(key, record)

    finally:
        manager.close()

    # Reopen and load the persistent tree.
    manager = PageManager(db_path)
    manager.open()

    try:
        tree = PersistentBTree(manager)
        tree.load_existing_tree()

        def check_subtree(page_id, lower=None, upper=None):
            page = manager.read_page(page_id)

            if page[0] == LEAF_NODE_TYPE:
                keys, records = decode_leaf_node(page)

                assert len(keys) == len(records)
                assert keys == sorted(keys)

                for key in keys:
                    if lower is not None:
                        assert key >= lower
                    if upper is not None:
                        assert key < upper

                return keys

            assert page[0] == INTERNAL_NODE_TYPE

            separators, children = decode_internal_node(page)

            assert separators == sorted(separators)
            assert len(children) == len(separators) + 1

            collected = []

            for index, child_id in enumerate(children):
                child_lower = (
                    lower if index == 0 else separators[index - 1]
                )
                child_upper = (
                    upper if index == len(separators) else separators[index]
                )

                collected.extend(
                    check_subtree(child_id, child_lower, child_upper)
                )

            return collected

        all_keys = check_subtree(tree.root_page_id)

        assert sorted(all_keys) == list(range(500))

        for key in [0, 73, 146, 250, 400, 499]:
            expected = struct.pack("<IQQ", 1, key * 100, 10)
            assert tree.search(key) == expected

    finally:
        manager.close()
def test_large_tree_persistence_after_reopen(tmp_path):
    import struct

    db_path = tmp_path / "large_tree_persistence.db"

    # Create and initialize the database.
    pm = PageManager(db_path)
    pm.initialize()
    pm.open()

    tree = PersistentBTree(pm)
    tree.create_empty_tree()

    # Insert 50,000 keys.
    for key in range(50_000):
        record = struct.pack("<IQQ", key, key * 100, key * 10)
        tree.insert_into_root(key, record)

    # Close the database.
    pm.close()

    # Reopen the same database file.
    pm2 = PageManager(db_path)
    pm2.open()

    tree2 = PersistentBTree(pm2)
    tree2.load_existing_tree()
    visited_pages, leaf_keys = collect_tree_structure(
        pm2, tree2.root_page_id
    )

    assert len(leaf_keys) == 50_000
    assert sorted(leaf_keys) == list(range(50_000))
    assert len(visited_pages) > 1
    # Verify records at different positions.
    for key in [0, 1, 100, 10_000, 25_000, 49_999]:
        expected = struct.pack("<IQQ", key, key * 100, key * 10)
        assert tree2.search(key) == expected

    # Verify a missing key.
    assert tree2.search(50_001) is None


    pm2.close()

def test_random_insertions_preserve_tree_structure(tmp_path):
    import random
    import struct

    db_path = tmp_path / "random_tree.db"

    pm = PageManager(db_path)
    pm.initialize()
    pm.open()

    try:
        tree = PersistentBTree(pm)
        tree.create_empty_tree()

        keys = list(range(50_000))
        random.Random(42).shuffle(keys)

        for key in keys:
            record = struct.pack(
                "<IQQ", key, key * 100, key * 10
            )
            tree.insert_into_root(key, record)

        visited_pages, leaf_keys = collect_tree_structure(
            pm, tree.root_page_id
        )

        assert len(leaf_keys) == 50_000
        assert sorted(leaf_keys) == list(range(50_000))
        assert len(visited_pages) > 1

        # Check selected records.
        for key in [0, 123, 10_000, 25_000, 49_999]:
            expected = struct.pack(
                "<IQQ", key, key * 100, key * 10
            )
            assert tree.search(key) == expected

    finally:
        pm.close()

def collect_tree_structure(page_manager, root_page_id):
    visited_pages = set()
    leaf_keys = []
    leaf_depth = None

    def visit(page_id, lower_bound=None, upper_bound=None, depth=0):
        nonlocal leaf_depth

        assert page_id not in visited_pages, (
            f"Page {page_id} was visited more than once"
        )
        visited_pages.add(page_id)

        page = page_manager.read_page(page_id)
        node_type, _ = struct.unpack_from("<BH", page, 0)

        if node_type == LEAF_NODE_TYPE:
            keys, records = decode_leaf_node(page)

            # All leaf nodes must be at the same depth.
            if leaf_depth is None:
                leaf_depth = depth
            else:
                assert depth == leaf_depth, (
                    f"Leaf page {page_id} is at depth {depth}, "
                    f"but expected depth {leaf_depth}"
                )

            # Keys within each leaf must be sorted.
            assert keys == sorted(keys), (
                f"Leaf page {page_id} has unsorted keys"
            )

            # Validate the key range allowed by the parent.
            if lower_bound is not None:
                assert all(key >= lower_bound for key in keys), (
                    f"Leaf page {page_id} contains keys below "
                    f"lower bound {lower_bound}"
                )

            if upper_bound is not None:
                assert all(key < upper_bound for key in keys), (
                    f"Leaf page {page_id} contains keys at or above "
                    f"upper bound {upper_bound}"
                )

            leaf_keys.extend(keys)
            return

        assert node_type == INTERNAL_NODE_TYPE, (
            f"Unknown node type on page {page_id}"
        )

        keys, children = decode_internal_node(page)

        # Separator keys must be sorted.
        assert keys == sorted(keys), (
            f"Internal page {page_id} has unsorted separators"
        )

        # An internal node must have one more child than separator.
        assert len(children) == len(keys) + 1, (
            f"Internal page {page_id} has an invalid child count"
        )

        for index, child_id in enumerate(children):
            child_lower = (
                lower_bound if index == 0 else keys[index - 1]
            )
            child_upper = (
                upper_bound if index == len(keys) else keys[index]
            )

            visit(
                child_id,
                child_lower,
                child_upper,
                depth + 1
            )

    visit(root_page_id)

    return visited_pages, leaf_keys


def test_large_tree_structure_is_valid(tmp_path):
    import struct

    db_path = tmp_path / "tree_structure.db"

    pm = PageManager(db_path)
    pm.initialize()
    pm.open()

    try:
        tree = PersistentBTree(pm)
        tree.create_empty_tree()

        for key in range(50_000):
            record = struct.pack(
                "<IQQ", key, key * 100, key * 10
            )
            tree.insert_into_root(key, record)

        visited_pages, leaf_keys = collect_tree_structure(
            pm, tree.root_page_id
        )

        # Every key must appear exactly once in reachable leaves.
        assert len(leaf_keys) == 50_000
        assert sorted(leaf_keys) == list(range(50_000))

        # Keys must also be globally sorted across leaf traversal.
        assert leaf_keys == list(range(50_000))

        # The tree should contain multiple reachable pages.
        assert len(visited_pages) > 1

    finally:
        pm.close()

def test_update_key_after_large_tree_splits(tmp_path):
    import struct

    db_path = tmp_path / "update_large_tree.db"

    pm = PageManager(db_path)
    pm.initialize()
    pm.open()

    try:
        tree = PersistentBTree(pm)
        tree.create_empty_tree()

        # Build a large tree.
        for key in range(50_000):
            record = struct.pack("<IQQ", key, key * 100, key * 10)
            tree.insert_into_root(key, record)

        # Update an existing key.
        key_to_update = 25_000
        updated_record = struct.pack(
            "<IQQ", key_to_update, 999_999, 888_888
        )
        tree.insert_into_root(key_to_update, updated_record)

        # The updated record must be returned.
        assert tree.search(key_to_update) == updated_record

        # Other records must remain unchanged.
        for key in [0, 100, 24_999, 25_001, 49_999]:
            expected = struct.pack("<IQQ", key, key * 100, key * 10)
            assert tree.search(key) == expected

        # Updating must not create a duplicate key.
        _, leaf_keys = collect_tree_structure(pm, tree.root_page_id)
        assert len(leaf_keys) == 50_000
        assert sorted(leaf_keys) == list(range(50_000))

    finally:
        pm.close()

def test_btree_boundary_cases(tmp_path):
    import struct

    db_path = tmp_path / "boundary_cases.db"

    pm = PageManager(db_path)
    pm.initialize()
    pm.open()

    try:
        tree = PersistentBTree(pm)
        tree.create_empty_tree()

        # An empty tree should not contain any keys.
        assert tree.search(0) is None

        test_keys = [0, 1, 10, 100, 1000]

        for key in test_keys:
            record = struct.pack("<IQQ", key, key * 100, key * 10)
            tree.insert_into_root(key, record)

        # Verify the smallest, largest, and intermediate keys.
        for key in test_keys:
            expected = struct.pack("<IQQ", key, key * 100, key * 10)
            assert tree.search(key) == expected

        # Missing keys should return None.
        assert tree.search(2) is None
        assert tree.search(999) is None
        assert tree.search(1001) is None

    finally:
        pm.close()

def test_btree_negative_keys(tmp_path):
    import struct

    db_path = tmp_path / "negative_keys.db"

    pm = PageManager(db_path)
    pm.initialize()
    pm.open()

    try:
        tree = PersistentBTree(pm)
        tree.create_empty_tree()

        keys = [-100, -10, -1, 0, 1, 10, 100]

        for key in keys:
            record = struct.pack(
                "<IQQ", abs(key), abs(key) * 100, abs(key) * 10
            )
            tree.insert_into_root(key, record)

        for key in keys:
            expected = struct.pack(
                "<IQQ", abs(key), abs(key) * 100, abs(key) * 10
            )
            assert tree.search(key) == expected

        assert tree.search(-50) is None
        assert tree.search(50) is None

    finally:
        pm.close()


def test_large_tree_internal_splits(tmp_path):
    db_path = tmp_path / "large_tree.db"

    page_manager = PageManager(db_path)
    page_manager.initialize()
    page_manager.open()
    tree = PersistentBTree(page_manager)
    tree.create_empty_tree()

    expected_records = {}

    for key in range(50_000):
        record = struct.pack("<IQQ", key, key * 100, key * 10)
        expected_records[key] = record
        tree.insert_into_root(key, record)

    # Verify every key and its record.
    for key, expected_record in expected_records.items():
        assert tree.search(key) == expected_record


import struct

from objectdb.page_manager import PageManager
from objectdb.persistent_btree import PersistentBTree
from objectdb.btree import (
    LEAF_NODE_TYPE,
    INTERNAL_NODE_TYPE,
    decode_leaf_node,
    decode_internal_node,
)


def create_test_tree(tmp_path, filename="delete.db"):
    """Create a fresh database and an empty B+ tree."""
    db_path = tmp_path / filename

    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    tree = PersistentBTree(manager)
    tree.create_empty_tree()

    return manager, tree


def insert_keys(tree, start, end):
    """Insert keys in the range [start, end)."""
    for key in range(start, end):
        record = struct.pack("<IQQ", 1, key * 100, 10)
        tree.insert_into_root(key, record)


def get_record(key):
    """Return the expected record reference for a key."""
    return struct.pack("<IQQ", 1, key * 100, 10)


def get_all_leaf_keys(manager, tree):
    """Collect leaf keys by traversing the tree from its root."""
    result = []

    def visit(page_id):
        page = manager.read_page(page_id)
        node_type = page[0]

        if node_type == LEAF_NODE_TYPE:
            keys, records = decode_leaf_node(page)
            assert len(keys) == len(records)
            result.extend(keys)

        elif node_type == INTERNAL_NODE_TYPE:
            keys, children = decode_internal_node(page)
            assert len(children) == len(keys) + 1

            for child_id in children:
                visit(child_id)

        else:
            raise AssertionError(f"Unknown node type: {node_type}")

    visit(tree.root_page_id)
    return result


def test_delete_existing_key_from_root_leaf(tmp_path):
    manager, tree = create_test_tree(tmp_path)

    try:
        insert_keys(tree, 10, 15)

        assert tree.delete(12) is True

        assert tree.search(12) is None
        assert tree.search(10) == get_record(10)
        assert tree.search(11) == get_record(11)
        assert tree.search(13) == get_record(13)
        assert tree.search(14) == get_record(14)

        keys, records = tree.read_root()
        assert keys == [10, 11, 13, 14]
        assert len(records) == 4

    finally:
        manager.close()


def test_delete_nonexistent_key(tmp_path):
    manager, tree = create_test_tree(tmp_path)

    try:
        insert_keys(tree, 10, 15)

        assert tree.delete(99) is False

        keys, records = tree.read_root()
        assert keys == [10, 11, 12, 13, 14]
        assert len(records) == 5

    finally:
        manager.close()


def test_delete_first_key_from_root_leaf(tmp_path):
    manager, tree = create_test_tree(tmp_path)

    try:
        insert_keys(tree, 10, 15)

        assert tree.delete(10) is True

        keys, records = tree.read_root()

        assert keys == [11, 12, 13, 14]
        assert len(records) == 4

    finally:
        manager.close()


def test_delete_all_keys_from_root_leaf(tmp_path):
    manager, tree = create_test_tree(tmp_path)

    try:
        insert_keys(tree, 10, 15)

        for key in range(10, 15):
            assert tree.delete(key) is True

        keys, records = tree.read_root()

        assert keys == []
        assert records == []

        assert tree.delete(10) is False

    finally:
        manager.close()


def test_delete_from_tree_with_multiple_leaves(tmp_path):
    manager, tree = create_test_tree(tmp_path)

    try:
        insert_keys(tree, 0, 200)

        for key in [0, 50, 100, 150, 199]:
            assert tree.delete(key) is True
            assert tree.search(key) is None

        for key in range(200):
            if key not in {0, 50, 100, 150, 199}:
                assert tree.search(key) == get_record(key)

        remaining_keys = get_all_leaf_keys(manager, tree)

        expected_keys = [
            key for key in range(200)
            if key not in {0, 50, 100, 150, 199}
        ]

        assert remaining_keys == expected_keys

    finally:
        manager.close()


def test_delete_causes_leaf_borrowing(tmp_path):
    manager, tree = create_test_tree(tmp_path)

    try:
        # Inserting 147 keys creates two leaves.
        insert_keys(tree, 0, 147)

        root_page = manager.read_page(tree.root_page_id)
        assert root_page[0] == INTERNAL_NODE_TYPE

        root_keys, child_ids = decode_internal_node(root_page)
        assert len(child_ids) == 2

        left_id, right_id = child_ids

        left_page = manager.read_page(left_id)
        right_page = manager.read_page(right_id)

        left_keys, _ = decode_leaf_node(left_page)
        right_keys, _ = decode_leaf_node(right_page)

        assert len(left_keys) == 73
        assert len(right_keys) == 74

        # Delete a key from the left leaf.
        # The left leaf underflows, and the right can lend.
        assert tree.delete(left_keys[-1]) is True

        left_page = manager.read_page(left_id)
        right_page = manager.read_page(right_id)

        left_keys, _ = decode_leaf_node(left_page)
        right_keys, _ = decode_leaf_node(right_page)

        assert len(left_keys) == 73
        assert len(right_keys) == 73

        assert get_all_leaf_keys(manager, tree) == [
            key for key in range(147)
            if key != 72
        ]

    finally:
        manager.close()


def test_delete_causes_leaf_merge_and_root_adjustment(tmp_path):
    manager, tree = create_test_tree(tmp_path)

    try:
        # This creates two leaves with 73 and 74 keys.
        insert_keys(tree, 0, 147)

        root_page_id_before = tree.root_page_id

        root_page = manager.read_page(root_page_id_before)
        assert root_page[0] == INTERNAL_NODE_TYPE

        _, child_ids = decode_internal_node(root_page)

        left_id, right_id = child_ids

        # Bring the right leaf down to the minimum of 73 keys.
        assert tree.delete(146) is True

        right_page = manager.read_page(right_id)
        right_keys, _ = decode_leaf_node(right_page)

        assert len(right_keys) == 73

        # Now deleting another right-leaf key causes underflow.
        # Neither leaf can lend, so they must merge.
        assert tree.delete(145) is True

        # The two leaves merge into one root leaf.
        assert tree.root_page_id != root_page_id_before

        new_root = manager.read_page(tree.root_page_id)
        assert new_root[0] == LEAF_NODE_TYPE

        merged_keys, merged_records = decode_leaf_node(new_root)

        assert merged_keys == list(range(145))
        assert len(merged_records) == 145

        for key in range(145):
            assert tree.search(key) == get_record(key)

        assert tree.search(145) is None
        assert tree.search(146) is None

    finally:
        manager.close()


def test_delete_keys_in_ascending_order(tmp_path):
    manager, tree = create_test_tree(tmp_path)

    try:
        insert_keys(tree, 0, 300)

        for key in range(300):
            assert tree.delete(key) is True
            assert tree.search(key) is None

        assert get_all_leaf_keys(manager, tree) == []

        # The tree should still be usable after all keys are deleted.
        tree.insert_into_root(999, get_record(999))

        assert tree.search(999) == get_record(999)

    finally:
        manager.close()


def test_delete_keys_in_descending_order(tmp_path):
    manager, tree = create_test_tree(tmp_path)

    try:
        insert_keys(tree, 0, 300)

        for key in range(299, -1, -1):
            assert tree.delete(key) is True
            assert tree.search(key) is None

        assert get_all_leaf_keys(manager, tree) == []

        tree.insert_into_root(999, get_record(999))

        assert tree.search(999) == get_record(999)

    finally:
        manager.close()


def test_delete_keys_in_random_order(tmp_path):
    import random

    manager, tree = create_test_tree(tmp_path)

    try:
        insert_keys(tree, 0, 500)

        keys = list(range(500))
        random.Random(42).shuffle(keys)

        for key in keys:
            assert tree.delete(key) is True
            assert tree.search(key) is None

        assert get_all_leaf_keys(manager, tree) == []

    finally:
        manager.close()


def test_delete_preserves_remaining_keys_after_mixed_operations(tmp_path):
    manager, tree = create_test_tree(tmp_path)

    try:
        insert_keys(tree, 0, 300)

        deleted_keys = {10, 20, 30, 100, 150, 200, 250, 299}

        for key in deleted_keys:
            assert tree.delete(key) is True

        for key in range(300):
            if key in deleted_keys:
                assert tree.search(key) is None
            else:
                assert tree.search(key) == get_record(key)

        remaining_keys = get_all_leaf_keys(manager, tree)

        assert remaining_keys == [
            key for key in range(300)
            if key not in deleted_keys
        ]

    finally:
        manager.close()