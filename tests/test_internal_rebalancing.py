
from objectdb.page_manager import PageManager
from objectdb.persistent_btree import PersistentBTree
from objectdb.btree import (
    encode_internal_node,
    decode_internal_node,
)


def create_test_database(tmp_path, filename="internal_delete.db"):
    db_path = tmp_path / filename

    manager = PageManager(db_path)
    manager.initialize()
    manager.open()

    tree = PersistentBTree(manager)
    tree.create_empty_tree()

    return manager, tree


def write_internal_node(manager, keys, children):
    page_id = manager.allocate_page()
    page = encode_internal_node(keys, children)
    manager.write_page(page_id, page)
    return page_id


def set_root(manager, tree, root_page_id):
    manager.update_root_page_id(root_page_id)
    tree.root_page_id = root_page_id


def read_internal_node(manager, page_id):
    page = manager.read_page(page_id)
    return decode_internal_node(page)


def test_borrow_from_left_internal_sibling(tmp_path):
    manager, tree = create_test_database(tmp_path)

    try:
        # The left sibling has 171 keys, so it can lend one.
        left_keys = list(range(171))

        # The target node has 169 keys, so it is underfull.
        node_keys = list(range(172, 341))

        left_children = list(range(1000, 1172))
        node_children = list(range(2000, 2170))

        left_id = write_internal_node(
            manager, left_keys, left_children
        )
        node_id = write_internal_node(
            manager, node_keys, node_children
        )

        # The separator is 171.
        root_id = write_internal_node(
            manager,
            [171],
            [left_id, node_id],
        )
        set_root(manager, tree, root_id)

        tree._borrow_from_left_internal(
            root_id,
            left_id,
            node_id,
            1,
        )

        updated_left_keys, updated_left_children = (
            read_internal_node(manager, left_id)
        )
        updated_node_keys, updated_node_children = (
            read_internal_node(manager, node_id)
        )
        updated_root_keys, updated_root_children = (
            read_internal_node(manager, root_id)
        )

        # The left sibling gives its largest key to the parent.
        assert updated_left_keys == list(range(170))

        # The old parent separator moves down into the target.
        assert updated_node_keys == list(range(171, 341))

        # The parent separator becomes the left sibling's old
        # largest key.
        assert updated_root_keys == [170]

        # One child pointer moves from left to target.
        assert len(updated_left_children) == len(updated_left_keys) + 1
        assert len(updated_node_children) == len(updated_node_keys) + 1

        assert updated_node_children[0] == left_children[-1]
        assert updated_root_children == [left_id, node_id]

    finally:
        manager.close()


def test_borrow_from_right_internal_sibling(tmp_path):
    manager, tree = create_test_database(
        tmp_path, "internal_right.db"
    )

    try:
        # The target node has 169 keys.
        node_keys = list(range(169))

        # The right sibling has 171 keys, so it can lend one.
        right_keys = list(range(170, 341))

        node_children = list(range(3000, 3170))
        right_children = list(range(4000, 4172))

        node_id = write_internal_node(
            manager, node_keys, node_children
        )
        right_id = write_internal_node(
            manager, right_keys, right_children
        )

        # The separator is 169.
        root_id = write_internal_node(
            manager,
            [169],
            [node_id, right_id],
        )
        set_root(manager, tree, root_id)

        tree._borrow_from_right_internal(
            root_id,
            node_id,
            right_id,
            0,
        )

        updated_node_keys, updated_node_children = (
            read_internal_node(manager, node_id)
        )
        updated_right_keys, updated_right_children = (
            read_internal_node(manager, right_id)
        )
        updated_root_keys, updated_root_children = (
            read_internal_node(manager, root_id)
        )

        # The parent separator moves down into the target.
        assert updated_node_keys == list(range(170))

        # The right sibling's smallest key moves up to the parent.
        assert updated_right_keys == list(range(171, 341))
        assert updated_root_keys == [170]

        # The right sibling's first child moves to the target.
        assert updated_node_children[-1] == right_children[0]

        assert len(updated_node_children) == len(updated_node_keys) + 1
        assert len(updated_right_children) == len(updated_right_keys) + 1
        assert updated_root_children == [node_id, right_id]

    finally:
        manager.close()


def test_merge_internal_nodes_includes_parent_separator(tmp_path):
    manager, tree = create_test_database(
        tmp_path, "internal_merge.db"
    )

    try:
        left_keys = list(range(169))
        right_keys = list(range(170, 339))

        left_children = list(range(5000, 5170))
        right_children = list(range(6000, 6170))

        left_id = write_internal_node(
            manager, left_keys, left_children
        )
        right_id = write_internal_node(
            manager, right_keys, right_children
        )

        # The separator 169 must be included in the merged node.
        root_id = write_internal_node(
            manager,
            [169],
            [left_id, right_id],
        )
        set_root(manager, tree, root_id)

        tree._merge_internal_nodes(
            root_id,
            left_id,
            right_id,
        )

        merged_keys, merged_children = read_internal_node(
            manager, left_id
        )
        parent_keys, parent_children = read_internal_node(
            manager, root_id
        )

        expected_keys = list(range(339))
        expected_children = left_children + right_children

        # The merged node contains the parent separator.
        assert merged_keys == expected_keys
        assert 169 in merged_keys

        # All child pointers are retained in their original order.
        assert merged_children == expected_children
        assert len(merged_children) == len(merged_keys) + 1

        # The parent loses the separator and right-child pointer.
        assert parent_keys == []
        assert parent_children == [left_id]

    finally:
        manager.close()


def test_recursive_merge_adjusts_root_and_persists_it(tmp_path):
    manager, tree = create_test_database(tmp_path)