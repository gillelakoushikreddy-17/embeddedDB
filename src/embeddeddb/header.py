import struct

MAGIC = b"OBJDB001"
FORMAT_VERSION = 1
PAGE_SIZE = 4096

# Magic value, format version, page size, root page ID
HEADER_FORMAT = "<8sIII"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # 20 bytes

EXTENDED_HEADER_FORMAT = "<8sIIII"
EXTENDED_HEADER_SIZE = struct.calcsize(EXTENDED_HEADER_FORMAT)  # 24 bytes

# 0 means the tree has not been created yet.
# Page 0 is reserved for the database header.
NO_ROOT_PAGE = 0
NO_CATALOG_PAGE = 0


def create_header(root_page_id=NO_ROOT_PAGE, catalog_page_id=None):
    if catalog_page_id is None:
        return struct.pack(
            HEADER_FORMAT,
            MAGIC,
            FORMAT_VERSION,
            PAGE_SIZE,
            root_page_id,
        )
    return struct.pack(
        EXTENDED_HEADER_FORMAT,
        MAGIC,
        FORMAT_VERSION,
        PAGE_SIZE,
        root_page_id,
        catalog_page_id,
    )


def read_header(data):
    if len(data) < HEADER_SIZE:
        raise ValueError("Database header is incomplete")

    magic, version, page_size, root_page_id = struct.unpack(
        HEADER_FORMAT,
        data[:HEADER_SIZE],
    )

    if magic != MAGIC:
        raise ValueError("Invalid database file")

    if version != FORMAT_VERSION:
        raise ValueError("Unsupported database format version")

    if page_size != PAGE_SIZE:
        raise ValueError("Unsupported page size")

    catalog_page_id = NO_CATALOG_PAGE
    if len(data) >= EXTENDED_HEADER_SIZE:
        catalog_page_id = struct.unpack_from("<I", data, HEADER_SIZE)[0]

    return {
        "version": version,
        "page_size": page_size,
        "root_page_id": root_page_id,
        "catalog_page_id": catalog_page_id,
    }
