
from pathlib import Path
import os
from objectdb.header import read_header, create_header, NO_ROOT_PAGE
PAGE_SIZE = 4096


class PageManager:
    def __init__(self, file_path):
        self.file_path = Path(file_path)
        self.file = None

    def open(self):
        self.file = open(self.file_path, "r+b")

        try:
            page_zero = self.file.read(PAGE_SIZE)

            if len(page_zero) != PAGE_SIZE:
                raise ValueError("Database header page is incomplete")

            read_header(page_zero)

            self.file.seek(0, 2)
            file_size = self.file.tell()

            if file_size % PAGE_SIZE != 0:
                raise ValueError("Database file contains an incomplete page")

            self.file.seek(0)

        except Exception:
            self.file.close()
            self.file = None
            raise

    def close(self):
        if self.file is not None:
            self.file.close()
            self.file = None

    def read_page(self, page_id):
        if not isinstance(page_id, int) or isinstance(page_id, bool) or page_id < 0:
            raise ValueError("Page ID must be a non-negative integer")
        offset = page_id * PAGE_SIZE

        self.file.seek(offset)
        data = self.file.read(PAGE_SIZE)

        if len(data) != PAGE_SIZE:
            raise ValueError("Page is incomplete or does not exist")

        return data

    def write_page(self, page_id, data):
        if not isinstance(page_id, int) or isinstance(page_id, bool) or page_id < 0:
            raise ValueError("Page ID must be a non-negative integer")
        if page_id == 0:
            raise ValueError("Cannot overwrite the database header page")
        if len(data) != PAGE_SIZE:
            raise ValueError("exactly 4096 bytes")

        offset = page_id * PAGE_SIZE

        self.file.seek(offset)
        self.file.write(data)
        self.file.flush()

    def initialize(self):
        from objectdb.header import create_header

        page_zero = create_header().ljust(PAGE_SIZE, b"\x00")

        with open(self.file_path, "xb") as file:
            file.write(page_zero)

    def allocate_page(self):
        self.file.seek(0, 2)
        file_size = self.file.tell()

        if file_size % PAGE_SIZE != 0:
            raise ValueError("Database file contains an incomplete page")

        page_id = file_size // PAGE_SIZE

        self.file.seek(0, 2)
        self.file.write(b"\x00" * PAGE_SIZE)
        self.file.flush()

        return page_id

    def update_root_page_id(self, root_page_id):
        if (
                not isinstance(root_page_id, int)
                or isinstance(root_page_id, bool)
                or root_page_id < 0
        ):
            raise ValueError("Root page ID must be a non-negative integer")

        self.file.seek(0)
        page_zero = self.file.read(PAGE_SIZE)
        header = read_header(page_zero)

        updated_header = create_header(root_page_id)
        updated_page = updated_header.ljust(PAGE_SIZE, b"\x00")

        self.file.seek(0)
        self.file.write(updated_page)
        self.file.flush()

    def sync(self):
        """Flush database changes to persistent storage."""
        if self.file is None:
            raise RuntimeError("Database file is not open")

        self.file.flush()
        os.fsync(self.file.fileno())

