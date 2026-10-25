
from objectdb.btree import BTree


def test_insert_and_search_100_keys():
    tree = BTree(max_keys=3)

    for i in range(100):
        key = f"file_{i:03}.jpg"
        tree.insert(key, i)

    for i in range(100):
        key = f"file_{i:03}.jpg"
        assert tree.search(key) == i

    assert tree.search("missing.jpg") is None


def test_tree_structure():
    tree = BTree(max_keys=3)

    for i in range(100):
        key = f"file_{i:03}.jpg"
        tree.insert(key, i)

    def validate(node, depth=0):
        assert len(node.keys) <= tree.max_keys
        assert node.keys == sorted(node.keys)

        if node.is_leaf:
            assert len(node.keys) == len(node.values)
            assert len(node.children) == 0
            return [depth]

        assert len(node.children) == len(node.keys) + 1

        depths = []

        for child in node.children:
            depths.extend(validate(child, depth + 1))

        return depths

    leaf_depths = validate(tree.root)

    assert len(set(leaf_depths)) == 1

def test_update_existing_key():
        tree = BTree(max_keys=3)

        tree.insert("photo.jpg", 100)
        tree.insert("photo.jpg", 200)

        assert tree.search("photo.jpg") == 200

def test_search_missing_key():
        tree = BTree(max_keys=3)

        tree.insert("a.jpg", 10)
        tree.insert("b.jpg", 20)

        assert tree.search("missing.jpg") is None

def test_reverse_insertion_order():
    tree = BTree(max_keys=3)

    for i in range(99, -1, -1):
        tree.insert(f"file_{i:03}.jpg", i)

    for i in range(100):
        assert tree.search(f"file_{i:03}.jpg") == i


def test_random_insertion_order():
    import random

    tree = BTree(max_keys=3)
    numbers = list(range(100))
    random.Random(42).shuffle(numbers)

    for i in numbers:
        tree.insert(f"file_{i:03}.jpg", i)

    for i in range(100):
        assert tree.search(f"file_{i:03}.jpg") == i

