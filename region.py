"""
This file is completely vibecoded and I don't care to undestand what's going on here
"""

import gzip
import zlib
from io import BytesIO
from pathlib import Path

import nbtlib

SECTOR_SIZE = 4096

# Chunk compression type -> decompressor.
DECOMPRESSORS = {
    1: gzip.decompress,
    2: zlib.decompress,
    3: lambda data: data,  # uncompressed
}


class Region:
    def __init__(self, path: Path):
        self.path = Path(path)

        # r.<x>.<z>.mca -> (x, z), needed to build .mcc paths for
        # oversized chunks stored outside the region file.
        _, rx, rz = self.path.stem.split(".")
        self.region_x, self.region_z = int(rx), int(rz)

        with open(self.path, "rb") as f:
            self.data = f.read()

    def get_chunk_location(self, cx: int, cz: int):
        index = (cz * 32 + cx) * 4
        entry = self.data[index:index + 4]

        # Empty/truncated region files (no chunks ever generated, or a
        # partially-written file) won't have a full 8KB header. Treat that
        # the same as "chunk not present" rather than crashing.
        if len(entry) < 4:
            return 0, 0

        offset = int.from_bytes(entry[:3], "big")
        sectors = entry[3]

        return offset, sectors

    def get_chunk_nbt(self, cx: int, cz: int):
        offset, _sectors = self.get_chunk_location(cx, cz)
        if offset == 0:
            return None

        start = offset * SECTOR_SIZE
        if start + 5 > len(self.data):
            return None

        length = int.from_bytes(self.data[start:start + 4], "big")
        flags = self.data[start + 4]

        # High bit set = chunk data lives in a separate c.<cx>.<cz>.mcc
        # file next to the region file (used when a chunk is too big to
        # fit inline). The remaining bits are the compression type,
        # regardless of where the data lives.
        compression = flags & 0x7F

        if flags & 0x80:
            world_cx = self.region_x * 32 + cx
            world_cz = self.region_z * 32 + cz
            mcc_path = self.path.parent / f"c.{world_cx}.{world_cz}.mcc"
            compressed = mcc_path.read_bytes()
        else:
            compressed = self.data[start + 5:start + 4 + length]

        try:
            decompress = DECOMPRESSORS[compression]
        except KeyError:
            raise ValueError(f"Unknown compression type: {compression}")

        raw = decompress(compressed)
        return nbtlib.File.parse(BytesIO(raw))