
from pathlib import Path


class Segment:
    def __init__(self, path: Path):
        self.path = path
        self.file = None

    def open(self):
        if self.file is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(self.path, "a+b")
        self.file.seek(0)


    def append(self, data: bytes) -> tuple[int, int]:
        if self.file is None:
            raise RuntimeError("Segment is not open")

        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("Data must be bytes-like")

        self.file.seek(0, 2)
        offset = self.file.tell()

        try:
            self.file.write(data)
            self.file.flush()
        except Exception:
            try:
                self.file.seek(offset)
                self.file.truncate()
                self.file.flush()
            except Exception:
                pass
            raise

        return offset, len(data)

    def read(self, offset: int, size: int) -> bytes:
        if self.file is None:
            raise RuntimeError("Segment is not open")

        if (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or offset < 0
            or size < 0
        ):
            raise ValueError("Offset and size must be non-negative integers")

        self.file.seek(0, 2)
        file_size = self.file.tell()
        if offset > file_size or offset + size > file_size:
            raise ValueError("Not enough data at the requested location")

        self.file.seek(offset)
        data = self.file.read(size)

        if len(data) != size:
            raise ValueError("Not enough data at the requested location")

        return data

    def sync(self):
        if self.file is None:
            raise RuntimeError("Segment is not open")

        import os

        self.file.flush()
        if hasattr(self.file, "fileno"):
            os.fsync(self.file.fileno())

    def close(self):
        if self.file is not None:
            self.file.close()
            self.file = None


