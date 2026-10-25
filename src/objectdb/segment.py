
from pathlib import Path


class Segment:
    def __init__(self, path: Path):
        self.path = path
        self.file = None

    def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(self.path, "a+b")
        self.file.seek(0)


    def append(self, data: bytes) -> tuple[int, int]:
        if self.file is None:
            raise RuntimeError("Segment is not open")

        self.file.seek(0, 2)
        offset = self.file.tell()

        self.file.write(data)
        self.file.flush()

        return offset, len(data)




    def read(self, offset: int, size: int) -> bytes:
        if self.file is None:
            raise RuntimeError("Segment is not open")

        if offset < 0 or size < 0:
            raise ValueError("Offset and size must be non-negative")

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
        os.fsync(self.file.fileno())

    def close(self):
        if self.file is not None:
            self.file.close()
            self.file = None


