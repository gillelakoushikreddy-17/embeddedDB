
import bisect
import struct

from objectdb.btree import (
    LEAF_NODE_TYPE,
    INTERNAL_NODE_TYPE,
    encode_leaf_node,
    decode_leaf_node,
    encode_internal_node,
    decode_internal_node,
)

MIN_KEY = -9223372036854775808
MAX_KEY = 9223372036854775807

def _validate_key(key):
    if not isinstance(key, int) or isinstance(key, bool):
        raise TypeError("Key must be an integer")
    if not (MIN_KEY <= key <= MAX_KEY):
        raise ValueError(f"Key {key} out of range for 64-bit signed integer [{MIN_KEY}, {MAX_KEY}]")


class PersistentBTree:
    def __init__(self, page_manager):
        self.page_manager = page_manager
        self.root_page_id = None

    def create_empty_tree(self):
        page_id = self.page_manager.allocate_page()

        empty_leaf = encode_leaf_node([], [])

        self.page_manager.write_page(page_id, empty_leaf)

        self.page_manager.update_root_page_id(page_id)

        self.root_page_id = page_id

        return page_id

    def read_root(self):
        if self.root_page_id is None:
            raise ValueError("Tree has not been created")

        page = self.page_manager.read_page(self.root_page_id)
        node_type, _ = struct.unpack_from("<BH", page, 0)

        if node_type == LEAF_NODE_TYPE:
            return decode_leaf_node(page)

        if node_type == INTERNAL_NODE_TYPE:
            return decode_internal_node(page)

        raise ValueError("Unknown B+ tree node type")

    def load_existing_tree(self):
        page_zero = self.page_manager.read_page(0)
        from objectdb.header import read_header

        header = read_header(page_zero)
        root_page_id = header["root_page_id"]

        if root_page_id == 0:
            raise ValueError("Database does not contain a B+ tree")

        self.root_page_id = root_page_id

        return root_page_id

    def insert_into_root(self, key, record):
        _validate_key(key)
        if self.root_page_id is None:
            raise ValueError("Tree has not been created")

        # Read the root to identify its type.
        root_page = self.page_manager.read_page(self.root_page_id)
        root_type, _ = struct.unpack_from("<BH", root_page, 0)

        # Case 1: The root is still a leaf.
        if root_type == LEAF_NODE_TYPE:
            leaf_page_id = self.root_page_id

        # Case 2: The root is internal; find the correct leaf.
        elif root_type == INTERNAL_NODE_TYPE:
            leaf_page_id = self._find_leaf_page(key)

        else:
            raise ValueError("Unknown B+ tree node type")

        page = self.page_manager.read_page(leaf_page_id)
        keys, records = decode_leaf_node(page)

        # Update an existing key using binary search.
        idx = bisect.bisect_left(keys, key)
        if idx < len(keys) and keys[idx] == key:
            records[idx] = record
            self.page_manager.write_page(
                leaf_page_id,
                encode_leaf_node(keys, records),
            )
            return

        # Insert the new key and its record at the sorted position.
        keys.insert(idx, key)
        records.insert(idx, record)

        # No split is needed if the leaf still has enough space.
        if len(keys) <= self._leaf_capacity():
            updated_page = encode_leaf_node(keys, records)
            self.page_manager.write_page(leaf_page_id, updated_page)
            return

        # A root leaf can split and create a new internal root.
        if leaf_page_id == self.root_page_id:
            (
                left_keys,
                left_records,
                right_keys,
                right_records,
            ) = self._split_leaf_entries(keys, records)

            left_page_id, right_page_id = self._write_split_leaves(
                left_keys,
                left_records,
                right_keys,
                right_records,
            )

            self._create_internal_root(
                left_page_id,
                right_page_id,
                right_keys[0],
            )
            return

        # The leaf is full, and it is not the root.
        parent_page_id = self._find_parent_page(leaf_page_id)

        if parent_page_id is None:
            raise ValueError("Could not find parent page")

        (
            left_keys,
            left_records,
            right_keys,
            right_records,
        ) = self._split_leaf_entries(keys, records)

        left_page_id, right_page_id = self._write_split_leaves(
            left_keys,
            left_records,
            right_keys,
            right_records,
        )

        self._insert_into_parent(
            parent_page_id,
            right_keys[0],
            leaf_page_id,  # Original child being replaced
            left_page_id,  # New left leaf
            right_page_id,  # New right leaf
        )

    def search(self, key):
        _validate_key(key)
        if self.root_page_id is None:
            raise ValueError("Tree has not been created")

        # Find the leaf that should contain this key.
        leaf_page_id = self._find_leaf_page(key)

        # Read and decode that leaf.
        leaf_page = self.page_manager.read_page(leaf_page_id)
        keys, records = decode_leaf_node(leaf_page)

        # Return the matching record, or None if absent.
        idx = bisect.bisect_left(keys, key)
        if idx < len(keys) and keys[idx] == key:
            return records[idx]

        return None

    def delete(self, key):
        _validate_key(key)
        if self.root_page_id is None:
            raise ValueError("Tree has not been created")

        # Find the leaf that should contain this key.
        leaf_page_id = self._find_leaf_page(key)

        # Read and decode the leaf.
        leaf_page = self.page_manager.read_page(leaf_page_id)
        keys, records = decode_leaf_node(leaf_page)

        # If the key doesn't exist, nothing needs to be deleted.
        idx = bisect.bisect_left(keys, key)
        if idx >= len(keys) or keys[idx] != key:
            return False

        # Remember whether we're deleting the first key.
        deleted_first_key = idx == 0

        # Remove the key and its corresponding record.
        keys.pop(idx)
        records.pop(idx)

        # Write the updated leaf back to disk.
        updated_page = encode_leaf_node(keys, records)
        self.page_manager.write_page(leaf_page_id, updated_page)

        # Update separators if the first key changed and the leaf
        # still has a key to use as its new first key.
        if deleted_first_key and keys:
            self._update_parent_separator(leaf_page_id)

        # Restore B+ tree balance after deletion.
        self._rebalance_after_delete(leaf_page_id)

        return True





    def _leaf_capacity(self):
        from objectdb.btree import (
            PAGE_SIZE,
            LEAF_HEADER_SIZE,
            KEY_SIZE,
            RECORD_SIZE,
        )

        entry_size = KEY_SIZE + RECORD_SIZE
        return (PAGE_SIZE - LEAF_HEADER_SIZE) // entry_size


    def _internal_capacity(self):
        from objectdb.btree import (
            PAGE_SIZE,
            INTERNAL_HEADER_SIZE,
            KEY_SIZE,
            PAGE_ID_SIZE,
        )

        return (
                PAGE_SIZE - INTERNAL_HEADER_SIZE - PAGE_ID_SIZE
        ) // (KEY_SIZE + PAGE_ID_SIZE)




    def _find_leaf_page(self, key):
        if self.root_page_id is None:
            raise ValueError("Tree has not been created")

        page_id = self.root_page_id

        while True:
            page = self.page_manager.read_page(page_id)

            node_type, _ = struct.unpack_from("<BH", page, 0)

            # Stop when we reach a leaf.
            if node_type == LEAF_NODE_TYPE:
                return page_id

            # Otherwise, the page must be an internal node.
            if node_type != INTERNAL_NODE_TYPE:
                raise ValueError("Unknown B+ tree node type")

            keys, child_page_ids = decode_internal_node(page)

            # Select the correct child using binary search.
            child_index = bisect.bisect_right(keys, key)
            page_id = child_page_ids[child_index]

    def _find_parent_page(self, child_page_id):
        if self.root_page_id is None:
            raise ValueError("Tree has not been created")

        if child_page_id == self.root_page_id:
            return None

        # Search the tree from the root.
        pages_to_visit = [self.root_page_id]

        while pages_to_visit:
            page_id = pages_to_visit.pop()
            page = self.page_manager.read_page(page_id)

            node_type, _ = struct.unpack_from("<BH", page, 0)

            # Leaves cannot contain child pointers.
            if node_type == LEAF_NODE_TYPE:
                continue

            if node_type != INTERNAL_NODE_TYPE:
                raise ValueError("Unknown B+ tree node type")

            _, child_page_ids = decode_internal_node(page)

            # We found the parent of the requested page.
            if child_page_id in child_page_ids:
                return page_id

            # Search internal children at the next level.
            pages_to_visit.extend(child_page_ids)

        return None



    def _get_siblings(self, page_id):
        """
        Return the parent page ID, left sibling page ID,
        right sibling page ID, and child index.
        """
        parent_page_id = self._find_parent_page(page_id)

        if parent_page_id is None:
            return None, None, None, None

        parent_page = self.page_manager.read_page(parent_page_id)
        _, child_page_ids = decode_internal_node(parent_page)

        child_index = child_page_ids.index(page_id)

        left_sibling_id = (
            child_page_ids[child_index - 1]
            if child_index > 0
            else None
        )

        right_sibling_id = (
            child_page_ids[child_index + 1]
            if child_index + 1 < len(child_page_ids)
            else None
        )

        return (
            parent_page_id,
            left_sibling_id,
            right_sibling_id,
            child_index,
        )



    def _borrow_from_left_leaf(
            self,
            parent_page_id,
            left_sibling_id,
            leaf_page_id,
            child_index,
    ):
        """Borrow the largest entry from the left sibling."""

        # Read the left sibling.
        left_page = self.page_manager.read_page(left_sibling_id)
        left_keys, left_records = decode_leaf_node(left_page)

        # Read the underfull leaf.
        leaf_page = self.page_manager.read_page(leaf_page_id)
        leaf_keys, leaf_records = decode_leaf_node(leaf_page)

        # The left sibling must have more than the minimum.
        minimum_keys = (self._leaf_capacity() + 1) // 2

        if len(left_keys) <= minimum_keys:
            raise ValueError("Left sibling cannot lend a key")

        # Remove the largest entry from the left sibling.
        borrowed_key = left_keys.pop()
        borrowed_record = left_records.pop()

        # Insert the borrowed entry at the beginning of this leaf.
        leaf_keys.insert(0, borrowed_key)
        leaf_records.insert(0, borrowed_record)

        # Write both updated leaves.
        self.page_manager.write_page(
            left_sibling_id,
            encode_leaf_node(left_keys, left_records),
        )

        self.page_manager.write_page(
            leaf_page_id,
            encode_leaf_node(leaf_keys, leaf_records),
        )

        # Update the separator between these two children.
        parent_page = self.page_manager.read_page(parent_page_id)
        parent_keys, child_page_ids = decode_internal_node(parent_page)

        parent_keys[child_index - 1] = leaf_keys[0]

        self.page_manager.write_page(
            parent_page_id,
            encode_internal_node(parent_keys, child_page_ids),
        )



    def _borrow_from_left_internal(
            self,
            parent_page_id,
            left_sibling_id,
            node_page_id,
            child_index,
    ):
        """Borrow a key and child pointer from the left internal sibling."""

        # Read the left sibling.
        left_page = self.page_manager.read_page(left_sibling_id)
        left_keys, left_children = decode_internal_node(left_page)

        # Read the underfull internal node.
        node_page = self.page_manager.read_page(node_page_id)
        node_keys, node_children = decode_internal_node(node_page)

        # The left sibling must have more than the minimum number of keys.
        minimum_keys = (self._internal_capacity() + 1) // 2

        if len(left_keys) <= minimum_keys:
            raise ValueError("Left internal sibling cannot lend a key")

        # Read the parent.
        parent_page = self.page_manager.read_page(parent_page_id)
        parent_keys, parent_children = decode_internal_node(parent_page)

        # The parent's separator moves down into the underfull node.
        parent_separator = parent_keys[child_index - 1]
        node_keys.insert(0, parent_separator)

        # The left sibling's largest separator moves up to the parent.
        parent_keys[child_index - 1] = left_keys.pop()

        # Move the left sibling's rightmost child to the beginning
        # of the underfull node's child list.
        borrowed_child = left_children.pop()
        node_children.insert(0, borrowed_child)

        # Write the updated left sibling.
        self.page_manager.write_page(
            left_sibling_id,
            encode_internal_node(left_keys, left_children),
        )

        # Write the updated underfull node.
        self.page_manager.write_page(
            node_page_id,
            encode_internal_node(node_keys, node_children),
        )

        # Write the updated parent.
        self.page_manager.write_page(
            parent_page_id,
            encode_internal_node(parent_keys, parent_children),
        )



    def _borrow_from_right_internal(
            self,
            parent_page_id,
            node_page_id,
            right_sibling_id,
            child_index,
    ):
        """Borrow a key and child pointer from the right internal sibling."""

        # Read the underfull internal node.
        node_page = self.page_manager.read_page(node_page_id)
        node_keys, node_children = decode_internal_node(node_page)

        # Read the right sibling.
        right_page = self.page_manager.read_page(right_sibling_id)
        right_keys, right_children = decode_internal_node(right_page)

        # The right sibling must have more than the minimum number of keys.
        minimum_keys = self._internal_capacity() // 2

        if len(right_keys) <= minimum_keys:
            raise ValueError("Right internal sibling cannot lend a key")

        # Read the parent.
        parent_page = self.page_manager.read_page(parent_page_id)
        parent_keys, parent_children = decode_internal_node(parent_page)

        # Move the parent's separator down to the end of this node.
        parent_separator = parent_keys[child_index]
        node_keys.append(parent_separator)

        # Move the right sibling's smallest separator up to the parent.
        parent_keys[child_index] = right_keys.pop(0)

        # Move the right sibling's leftmost child to this node.
        borrowed_child = right_children.pop(0)
        node_children.append(borrowed_child)

        # Write the updated underfull node.
        self.page_manager.write_page(
            node_page_id,
            encode_internal_node(node_keys, node_children),
        )

        # Write the updated right sibling.
        self.page_manager.write_page(
            right_sibling_id,
            encode_internal_node(right_keys, right_children),
        )

        # Write the updated parent.
        self.page_manager.write_page(
            parent_page_id,
            encode_internal_node(parent_keys, parent_children),
        )




    def _borrow_from_right_leaf(
            self,
            parent_page_id,
            leaf_page_id,
            right_sibling_id,
            child_index,
    ):
        """Borrow the smallest entry from the right sibling."""

        # Read the underfull leaf.
        leaf_page = self.page_manager.read_page(leaf_page_id)
        leaf_keys, leaf_records = decode_leaf_node(leaf_page)

        # Read the right sibling.
        right_page = self.page_manager.read_page(right_sibling_id)
        right_keys, right_records = decode_leaf_node(right_page)

        # The right sibling must have more than the minimum.
        minimum_keys = (self._leaf_capacity() + 1) // 2

        if len(right_keys) <= minimum_keys:
            raise ValueError("Right sibling cannot lend a key")

        # Remove the smallest entry from the right sibling.
        borrowed_key = right_keys.pop(0)
        borrowed_record = right_records.pop(0)

        # Append the borrowed entry to the underfull leaf.
        leaf_keys.append(borrowed_key)
        leaf_records.append(borrowed_record)

        # Write both updated leaves.
        self.page_manager.write_page(
            leaf_page_id,
            encode_leaf_node(leaf_keys, leaf_records),
        )

        self.page_manager.write_page(
            right_sibling_id,
            encode_leaf_node(right_keys, right_records),
        )

        # Update the separator to the right sibling's new first key.
        parent_page = self.page_manager.read_page(parent_page_id)
        parent_keys, child_page_ids = decode_internal_node(parent_page)

        parent_keys[child_index] = right_keys[0]

        self.page_manager.write_page(
            parent_page_id,
            encode_internal_node(parent_keys, child_page_ids),
        )



    def _merge_leaf_nodes(
            self,
            parent_page_id,
            left_page_id,
            right_page_id,
    ):
        """Merge two adjacent leaf nodes into the left leaf."""

        # Read the left leaf.
        left_page = self.page_manager.read_page(left_page_id)
        left_keys, left_records = decode_leaf_node(left_page)

        # Read the right leaf.
        right_page = self.page_manager.read_page(right_page_id)
        right_keys, right_records = decode_leaf_node(right_page)

        # Combine keys and their corresponding records.
        merged_keys = left_keys + right_keys
        merged_records = left_records + right_records

        # Ensure the merged leaf fits into one page.
        if len(merged_keys) > self._leaf_capacity():
            raise ValueError("Merged leaf exceeds page capacity")

        # Write the merged contents into the left leaf.
        self.page_manager.write_page(
            left_page_id,
            encode_leaf_node(merged_keys, merged_records),
        )

        # Remove the right child's pointer and its separator.
        parent_page = self.page_manager.read_page(parent_page_id)
        parent_keys, child_page_ids = decode_internal_node(parent_page)

        right_index = child_page_ids.index(right_page_id)

        if right_index == 0:
            raise ValueError("Right leaf is not positioned after its left sibling")

        if child_page_ids[right_index - 1] != left_page_id:
            raise ValueError("Leaves are not adjacent siblings")

        parent_keys.pop(right_index - 1)
        child_page_ids.pop(right_index)

        # Persist the updated parent.
        self.page_manager.write_page(
            parent_page_id,
            encode_internal_node(parent_keys, child_page_ids),
        )



    def _merge_internal_nodes(
            self,
            parent_page_id,
            left_page_id,
            right_page_id,
    ):
        """Merge two adjacent internal nodes into the left node."""

        # Read the left internal node.
        left_page = self.page_manager.read_page(left_page_id)
        left_keys, left_children = decode_internal_node(left_page)

        # Read the right internal node.
        right_page = self.page_manager.read_page(right_page_id)
        right_keys, right_children = decode_internal_node(right_page)

        # Read the parent.
        parent_page = self.page_manager.read_page(parent_page_id)
        parent_keys, parent_children = decode_internal_node(parent_page)

        # Find the right node's position in the parent.
        right_index = parent_children.index(right_page_id)

        # The right node must immediately follow the left node.
        if right_index == 0:
            raise ValueError("Right node has no left sibling")

        if parent_children[right_index - 1] != left_page_id:
            raise ValueError("Internal nodes are not adjacent siblings")

        # The separator between the two nodes belongs in the merged node.
        parent_separator = parent_keys[right_index - 1]

        # Combine keys and child pointers.
        merged_keys = left_keys + [parent_separator] + right_keys
        merged_children = left_children + right_children

        # Ensure the merged node fits in one page.
        if len(merged_keys) > self._internal_capacity():
            raise ValueError("Merged internal node exceeds page capacity")

        # Write the merged node into the left page.
        self.page_manager.write_page(
            left_page_id,
            encode_internal_node(merged_keys, merged_children),
        )

        # Remove the separator and the right child's pointer from the parent.
        parent_keys.pop(right_index - 1)
        parent_children.pop(right_index)

        # Write the updated parent.
        self.page_manager.write_page(
            parent_page_id,
            encode_internal_node(parent_keys, parent_children),
        )

    def _rebalance_after_delete(self, page_id):
        """Restore B+ tree balance after deleting a key."""

        page = self.page_manager.read_page(page_id)
        node_type = page[0]

        # STEP 1: Handle the root.
        if page_id == self.root_page_id:

            # An internal root with zero keys should have one child.
            if node_type == INTERNAL_NODE_TYPE:
                keys, children = decode_internal_node(page)

                if len(keys) == 0:
                    if len(children) != 1:
                        raise ValueError(
                            "An empty internal root must have exactly one child"
                        )

                    # Promote the only child to become the new root.
                    new_root_id = children[0]
                    self.page_manager.update_root_page_id(new_root_id)
                    self.root_page_id = new_root_id

            # A root leaf may legally contain zero keys.
            return

        # STEP 2: Find the parent and siblings.
        parent_id, left_id, right_id, child_index = (
            self._get_siblings(page_id)
        )

        if parent_id is None:
            raise ValueError("Non-root node has no parent")

        # STEP 3: Handle leaf-node underflow.
        if node_type == LEAF_NODE_TYPE:

            keys, records = decode_leaf_node(page)

            # Minimum number of keys in a non-root leaf.
            minimum_keys = (self._leaf_capacity() + 1) // 2

            # No underflow: nothing to do.
            if len(keys) >= minimum_keys:
                return

            # STEP 3A: Try borrowing from the left sibling.
            if left_id is not None:
                left_page = self.page_manager.read_page(left_id)
                left_keys, _ = decode_leaf_node(left_page)

                if len(left_keys) > minimum_keys:
                    self._borrow_from_left_leaf(
                        parent_id,
                        left_id,
                        page_id,
                        child_index,
                    )
                    return

            # STEP 3B: Try borrowing from the right sibling.
            if right_id is not None:
                right_page = self.page_manager.read_page(right_id)
                right_keys, _ = decode_leaf_node(right_page)

                if len(right_keys) > minimum_keys:
                    self._borrow_from_right_leaf(
                        parent_id,
                        page_id,
                        right_id,
                        child_index,
                    )

                    # Refresh this leaf's separator if necessary.
                    self._update_parent_separator(page_id)
                    return

            # STEP 3C: Neither sibling can lend.
            # Merge with the left sibling if one exists.
            if left_id is not None:
                self._merge_leaf_nodes(
                    parent_id,
                    left_id,
                    page_id,
                )

            # Otherwise, merge with the right sibling.
            elif right_id is not None:
                self._merge_leaf_nodes(
                    parent_id,
                    page_id,
                    right_id,
                )

            else:
                raise ValueError("Underflowing leaf has no siblings")

            # The merge removed a separator from the parent.
            # The parent might now underflow.
            self._rebalance_after_delete(parent_id)
            return

        # STEP 4: Handle internal-node underflow.
        if node_type == INTERNAL_NODE_TYPE:

            keys, children = decode_internal_node(page)

            # Minimum keys in a non-root internal node.
            minimum_keys = self._internal_capacity() // 2

            # No underflow: nothing to do.
            if len(keys) >= minimum_keys:
                return

            # STEP 4A: Try borrowing from the left sibling.
            if left_id is not None:
                left_page = self.page_manager.read_page(left_id)
                left_keys, _ = decode_internal_node(left_page)

                if len(left_keys) > minimum_keys:
                    self._borrow_from_left_internal(
                        parent_id,
                        left_id,
                        page_id,
                        child_index,
                    )
                    return

            # STEP 4B: Try borrowing from the right sibling.
            if right_id is not None:
                right_page = self.page_manager.read_page(right_id)
                right_keys, _ = decode_internal_node(right_page)

                if len(right_keys) > minimum_keys:
                    self._borrow_from_right_internal(
                        parent_id,
                        page_id,
                        right_id,
                        child_index,
                    )
                    return

            # STEP 4C: Neither sibling can lend.
            # Merge with the left sibling if one exists.
            if left_id is not None:
                self._merge_internal_nodes(
                    parent_id,
                    left_id,
                    page_id,
                )

            # Otherwise, merge with the right sibling.
            elif right_id is not None:
                self._merge_internal_nodes(
                    parent_id,
                    page_id,
                    right_id,
                )

            else:
                raise ValueError("Underflowing internal node has no siblings")

            # The parent lost a separator and might underflow too.
            self._rebalance_after_delete(parent_id)
            return

        # STEP 5: Reject an unrecognized node type.
        raise ValueError(f"Unknown node type: {node_type}")

    def _update_parent_separator(self, child_page_id):
        """
        Update ancestor separators if the first key in a
        subtree has changed.
        """
        page = self.page_manager.read_page(child_page_id)
        node_type, _ = struct.unpack_from("<BH", page, 0)

        # Find the first key in this subtree.
        while node_type == INTERNAL_NODE_TYPE:
            _, child_page_ids = decode_internal_node(page)

            page = self.page_manager.read_page(child_page_ids[0])
            node_type, _ = struct.unpack_from("<BH", page, 0)

        if node_type != LEAF_NODE_TYPE:
            raise ValueError("Unknown B+ tree node type")

        keys, _ = decode_leaf_node(page)

        # An empty subtree has no first key to use as a separator.
        if not keys:
            return

        first_key = keys[0]
        current_page_id = child_page_id

        while current_page_id != self.root_page_id:
            parent_page_id = self._find_parent_page(current_page_id)

            if parent_page_id is None:
                raise ValueError("Could not find parent page")

            parent_page = self.page_manager.read_page(parent_page_id)
            parent_keys, child_page_ids = decode_internal_node(parent_page)

            child_index = child_page_ids.index(current_page_id)

            if child_index > 0:
                # This subtree is a right child.
                # Its first key determines this separator.
                parent_keys[child_index - 1] = first_key

                self.page_manager.write_page(
                    parent_page_id,
                    encode_internal_node(parent_keys, child_page_ids),
                )
                return

            # This subtree is the parent's first child.
            # Its first key does not determine a separator here,
            # so the change may need to propagate to an ancestor.
            current_page_id = parent_page_id

    def _insert_separator_into_parent(
            self,
            parent_page_id,
            separator_key,
            left_page_id,
            right_page_id,
    ):
        page = self.page_manager.read_page(parent_page_id)
        keys, child_page_ids = decode_internal_node(page)

        # Find the old left child in the parent.
        child_index = child_page_ids.index(left_page_id)

        # Insert the separator and the new right child.
        keys.insert(child_index, separator_key)
        child_page_ids.insert(child_index + 1, right_page_id)

        updated_page = encode_internal_node(keys, child_page_ids)
        self.page_manager.write_page(parent_page_id, updated_page)

    def _split_leaf_entries(self, keys, records):
        if len(keys) != len(records):
            raise ValueError("Each key must have one record reference")

        if len(keys) <= self._leaf_capacity():
            raise ValueError("Leaf does not need splitting")

        midpoint = len(keys) // 2

        left_keys = keys[:midpoint]
        left_records = records[:midpoint]

        right_keys = keys[midpoint:]
        right_records = records[midpoint:]

        return (
            left_keys,
            left_records,
            right_keys,
            right_records,
        )

    def _write_split_leaves(self, left_keys, left_records,
                            right_keys, right_records):
        left_page_id = self.page_manager.allocate_page()
        right_page_id = self.page_manager.allocate_page()

        left_page = encode_leaf_node(left_keys, left_records)
        right_page = encode_leaf_node(right_keys, right_records)

        self.page_manager.write_page(left_page_id, left_page)
        self.page_manager.write_page(right_page_id, right_page)

        return left_page_id, right_page_id

    def _create_internal_root(self, left_page_id, right_page_id,
                              separator_key):
        root_page_id = self.page_manager.allocate_page()

        root_page = encode_internal_node(
            [separator_key],
            [left_page_id, right_page_id],
        )

        self.page_manager.write_page(root_page_id, root_page)
        self.page_manager.update_root_page_id(root_page_id)

        self.root_page_id = root_page_id

        return root_page_id


    def _insert_into_parent(
        self,
        parent_page_id,
        separator_key,
        old_page_id,
        left_page_id,
        right_page_id,
    ):
        parent_page = self.page_manager.read_page(parent_page_id)
        keys, child_page_ids = decode_internal_node(parent_page)

        # Insert the separator and replace the old child.
        child_index = child_page_ids.index(old_page_id)
        keys.insert(child_index, separator_key)
        child_page_ids[child_index:child_index + 1] = [
            left_page_id,
            right_page_id,
        ]

        from objectdb.btree import (
            PAGE_SIZE,
            INTERNAL_HEADER_SIZE,
            KEY_SIZE,
            PAGE_ID_SIZE,
        )

        max_keys = (
            PAGE_SIZE - INTERNAL_HEADER_SIZE - PAGE_ID_SIZE
        ) // (KEY_SIZE + PAGE_ID_SIZE)

        # Case 1: The updated parent still fits.
        if len(keys) <= max_keys:
            updated_parent = encode_internal_node(
                keys, child_page_ids
            )
            self.page_manager.write_page(
                parent_page_id, updated_parent
            )
            return

        # Case 2: The internal node overflows.
        (
            left_keys,
            left_children,
            promoted_key,
            right_keys,
            right_children,
        ) = self._split_internal_entries(keys, child_page_ids)

        left_internal_id, right_internal_id = (
            self._write_split_internal_nodes(
                left_keys,
                left_children,
                right_keys,
                right_children,
            )
        )

        # If the overflowing node is the root,
        # create a new root above the two split nodes.
        if parent_page_id == self.root_page_id:
            self._create_internal_root(
                left_internal_id,
                right_internal_id,
                promoted_key,
            )
            return

        # Otherwise, propagate the promoted key upward.
        grandparent_page_id = self._find_parent_page(
            parent_page_id
        )

        if grandparent_page_id is None:
            raise ValueError(
                "Could not find grandparent page"
            )

        self._insert_into_parent(
            grandparent_page_id,
            promoted_key,
            parent_page_id,
            left_internal_id,
            right_internal_id,
        )


    def _split_internal_entries(self, keys, child_page_ids):
        """Split internal-node entries around a promoted middle key."""
        middle_index = len(keys) // 2

        promoted_key = keys[middle_index]

        left_keys = keys[:middle_index]
        right_keys = keys[middle_index + 1:]

        left_children = child_page_ids[:middle_index + 1]
        right_children = child_page_ids[middle_index + 1:]

        return (
            left_keys,
            left_children,
            promoted_key,
            right_keys,
            right_children,
        )

    def _write_split_internal_nodes(
            self,
            left_keys,
            left_children,
            right_keys,
            right_children,
    ):
        left_page_id = self.page_manager.allocate_page()
        right_page_id = self.page_manager.allocate_page()

        left_page = encode_internal_node(
            left_keys, left_children
        )
        right_page = encode_internal_node(
            right_keys, right_children
        )

        self.page_manager.write_page(left_page_id, left_page)
        self.page_manager.write_page(right_page_id, right_page)

        return left_page_id, right_page_id




