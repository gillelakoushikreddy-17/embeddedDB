
import struct

PAGE_SIZE = 4096

INTERNAL_NODE_TYPE = 1
INTERNAL_HEADER_FORMAT = "<BH"
INTERNAL_HEADER_SIZE = struct.calcsize(INTERNAL_HEADER_FORMAT)

KEY_FORMAT = "<q"
KEY_SIZE = struct.calcsize(KEY_FORMAT)

PAGE_ID_FORMAT = "<I"
PAGE_ID_SIZE = struct.calcsize(PAGE_ID_FORMAT)

LEAF_NODE_TYPE = 2
LEAF_HEADER_FORMAT = "<BH"
LEAF_HEADER_SIZE = struct.calcsize(LEAF_HEADER_FORMAT)

RECORD_FORMAT = "<IQQ"
RECORD_SIZE = struct.calcsize(RECORD_FORMAT)

def encode_internal_node(keys, child_page_ids):
    if len(child_page_ids) != len(keys) + 1:
        raise ValueError("An internal node needs one more child than key")

    for i in range(len(keys) - 1):
        if keys[i] >= keys[i + 1]:
            raise ValueError("Internal keys must be strictly sorted without duplicates")

    key_count = len(keys)
    required_size = (
        INTERNAL_HEADER_SIZE
        + key_count * KEY_SIZE
        + (key_count + 1) * PAGE_ID_SIZE
    )

    if required_size > PAGE_SIZE:
        raise ValueError("Internal node does not fit in one page")

    page = bytearray(PAGE_SIZE)

    struct.pack_into(
        INTERNAL_HEADER_FORMAT,
        page,
        0,
        INTERNAL_NODE_TYPE,
        key_count,
    )

    offset = INTERNAL_HEADER_SIZE
    if key_count > 0:
        struct.pack_into(f"<{key_count}q", page, offset, *keys)
        offset += key_count * KEY_SIZE

    struct.pack_into(f"<{key_count + 1}I", page, offset, *child_page_ids)

    return bytes(page)


def decode_internal_node(page):
    if len(page) != PAGE_SIZE:
        raise ValueError("Page must be exactly 4096 bytes")

    node_type, key_count = struct.unpack_from(
        INTERNAL_HEADER_FORMAT, page, 0
    )

    if node_type != INTERNAL_NODE_TYPE:
        raise ValueError("Page is not an internal node")

    required_size = (
        INTERNAL_HEADER_SIZE
        + key_count * KEY_SIZE
        + (key_count + 1) * PAGE_ID_SIZE
    )

    if required_size > PAGE_SIZE:
        raise ValueError("Invalid internal-node key count")

    offset = INTERNAL_HEADER_SIZE
    keys = list(struct.unpack_from(f"<{key_count}q", page, offset))
    child_page_ids = list(
        struct.unpack_from(f"<{key_count + 1}I", page, offset + key_count * KEY_SIZE)
    )

    for i in range(len(keys) - 1):
        if keys[i] >= keys[i + 1]:
            raise ValueError("Internal keys must be strictly sorted without duplicates")

    return keys, child_page_ids


def encode_leaf_node(keys, records):
    if len(keys) != len(records):
        raise ValueError("Each key must have one record reference")

    for i in range(len(keys) - 1):
        if keys[i] >= keys[i + 1]:
            raise ValueError("Leaf keys must be strictly sorted without duplicates")

    page = bytearray(PAGE_SIZE)

    struct.pack_into(
        LEAF_HEADER_FORMAT,
        page,
        0,
        LEAF_NODE_TYPE,
        len(keys),
    )

    offset = LEAF_HEADER_SIZE

    for key, record in zip(keys, records):
        struct.pack_into(KEY_FORMAT, page, offset, key)
        offset += KEY_SIZE

        if len(record) != RECORD_SIZE:
            raise ValueError("Each record reference must be 20 bytes")

        page[offset:offset + RECORD_SIZE] = record
        offset += RECORD_SIZE

    if offset > PAGE_SIZE:
        raise ValueError("Leaf node does not fit in one page")

    return bytes(page)


def decode_leaf_node(page):
    if len(page) != PAGE_SIZE:
        raise ValueError("Page must be exactly 4096 bytes")

    node_type, key_count = struct.unpack_from(
        LEAF_HEADER_FORMAT, page, 0
    )

    if node_type != LEAF_NODE_TYPE:
        raise ValueError("Page is not a leaf node")

    required_size = LEAF_HEADER_SIZE + key_count * (KEY_SIZE + RECORD_SIZE)

    if required_size > PAGE_SIZE:
        raise ValueError("Invalid leaf-node key count")

    keys = []
    records = []
    offset = LEAF_HEADER_SIZE
    entry_size = KEY_SIZE + RECORD_SIZE

    for _ in range(key_count):
        key = struct.unpack_from(KEY_FORMAT, page, offset)[0]
        keys.append(key)
        record = bytes(page[offset + KEY_SIZE:offset + entry_size])
        records.append(record)
        offset += entry_size

    for i in range(len(keys) - 1):
        if keys[i] >= keys[i + 1]:
            raise ValueError("Leaf keys must be strictly sorted without duplicates")

    return keys, records


class BTreeNode:
    def __init__(self, is_leaf=False):
        self.is_leaf = is_leaf
        self.keys = []
        self.values = []
        self.children = []



class BTree:
    def __init__(self, max_keys=3):
        self.root = BTreeNode(is_leaf=True)
        self.max_keys = max_keys


    def search(self, key):
        node = self.root

        # Step 1: Follow child nodes until we reach a leaf
        while not node.is_leaf:
            i = 0

            while i < len(node.keys) and key >= node.keys[i]:
                i += 1

            node = node.children[i]

        # Step 2: Search for the exact key inside the leaf
        for i in range(len(node.keys)):
            if node.keys[i] == key:
                return node.values[i]

        return None






    def insert(self, key, value):
        node = self.root
        path = []

        # 1. Find the correct leaf and remember the ancestors
        while not node.is_leaf:
            path.append(node)

            i = 0
            while i < len(node.keys) and key >= node.keys[i]:
                i += 1

            node = node.children[i]

        # 2. Update the value if the key already exists
        for i in range(len(node.keys)):
            if node.keys[i] == key:
                node.values[i] = value
                return

        # 3. Insert the new key-value pair in sorted order
        node.keys.append(key)
        node.values.append(value)

        sorted_items = sorted(
            zip(node.keys, node.values),
            key=lambda item: item[0]
        )

        node.keys = [item[0] for item in sorted_items]
        node.values = [item[1] for item in sorted_items]

        # 4. Continue only if a node overflows
        while len(node.keys) > self.max_keys:

            if node.is_leaf:
                # Split a leaf
                middle = len(node.keys) // 2

                right_node = BTreeNode(is_leaf=True)
                right_node.keys = node.keys[middle:]
                right_node.values = node.values[middle:]

                node.keys = node.keys[:middle]
                node.values = node.values[:middle]

                # Copy the first key of the right leaf upward
                separator = right_node.keys[0]

            else:
                # Split an internal node
                middle = len(node.keys) // 2

                separator = node.keys[middle]

                right_node = BTreeNode(is_leaf=False)
                right_node.keys = node.keys[middle + 1:]
                right_node.children = node.children[middle + 1:]

                node.keys = node.keys[:middle]
                node.children = node.children[:middle + 1]

            # 5. If there is no parent, create a new root
            if not path:
                new_root = BTreeNode(is_leaf=False)
                new_root.keys = [separator]
                new_root.children = [node, right_node]
                self.root = new_root
                return

            # 6. Insert the separator into the parent
            parent = path.pop()
            insert_index = parent.children.index(node)

            parent.keys.insert(insert_index, separator)
            parent.children.insert(insert_index + 1, right_node)

            # Continue upward: the parent may now overflow
            node = parent











