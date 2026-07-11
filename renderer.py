import numpy as np

from pathlib import Path
from PIL import Image

from colors import BLOCK_ID, WATER_BLOCKS, COLORS
from region import Region

VOID_COLOR = (0, 0, 0, 0)  # fully transparent
WORLD_MIN_Y = -64

# How many chunk/region rows to keep around while rendering: the row we're
# currently on, plus the previous one (its data may still be referenced by
# in-flight lookups such as water depth on the just-finished row).
CHUNK_ROWS_TO_KEEP = 2
REGION_ROWS_TO_KEEP = 2

UNKNOWN_BLOCKS = set()
REGION_CACHE = {}
CHUNK_CACHE = {}  # (chunk_x, chunk_z) -> prepared chunk dict


def unpack_bits(data, index, bits):
    values_per_long = 64 // bits
    value = int(data[index // values_per_long])
    bit_index = (index % values_per_long) * bits
    return (value >> bit_index) & ((1 << bits) - 1)


def unpack_heightmap(data, x, z):
    return unpack_bits(data, z * 16 + x, 9)


def _prune(cache, row_of, current_row, rows_to_keep):
    """Drop entries whose row is more than `rows_to_keep` behind current_row."""
    min_row = current_row - (rows_to_keep - 1)
    for key in [k for k in cache if row_of(k) < min_row]:
        del cache[key]


def prepare_chunk(chunk):
    """Flatten a raw NBT chunk into cheap-to-query section data.

    Each section becomes (block_ids, bits, data):
      block_ids: list[int]  -- palette index -> resolved block id (0 if
                                unknown/air), computed once instead of per
                                block lookup.
      bits:      int        -- bits-per-block for the packed `data` longs,
                                or 0 if the section is a single-block palette.
      data:      the packed long array, or None if bits == 0.
    """
    sections = {}
    for section in chunk["sections"]:
        block_states = section.get("block_states")
        if block_states is None:
            continue

        palette = block_states["palette"]
        block_ids = []
        for entry in palette:
            name = str(entry["Name"])
            block_id = BLOCK_ID.get(name, 0)
            if block_id == 0 and name not in BLOCK_ID:
                UNKNOWN_BLOCKS.add(name)
            block_ids.append(block_id)

        if len(palette) == 1 or "data" not in block_states:
            bits, data = 0, None
        else:
            bits = max(4, (len(palette) - 1).bit_length())
            data = block_states["data"]

        sections[int(section["Y"])] = (block_ids, bits, data)

    heightmap = chunk.get("Heightmaps", {}).get("WORLD_SURFACE")
    return {"heightmap": heightmap, "sections": sections}


def get_prepared_chunk(world_x, world_z):
    chunk_x, local_x = divmod(world_x, 16)
    chunk_z, local_z = divmod(world_z, 16)

    cache_key = (chunk_x, chunk_z)
    prepared = CHUNK_CACHE.get(cache_key)
    if prepared is not None:
        return prepared, local_x, local_z

    region_key = (chunk_x >> 5, chunk_z >> 5)
    region = REGION_CACHE.get(region_key)
    if region is None:
        path = WORLD_PATH / "region" / f"r.{region_key[0]}.{region_key[1]}.mca"
        if not path.exists():
            return None, None, None
        region = REGION_CACHE[region_key] = Region(path)

    chunk = region.get_chunk_nbt(chunk_x & 31, chunk_z & 31)
    if chunk is None:
        return None, None, None

    prepared = CHUNK_CACHE[cache_key] = prepare_chunk(chunk)
    return prepared, local_x, local_z


def get_block_id(prepared, local_x, y, local_z):
    """Resolved block id at a local column position, 0 for air/unknown."""
    section = prepared["sections"].get(y >> 4)
    if section is None:
        return 0

    block_ids, bits, data = section
    if bits == 0:
        return block_ids[0]

    index = (y & 15) * 256 + local_z * 16 + local_x
    return block_ids[unpack_bits(data, index, bits)]


def get_top_block(prepared, local_x, local_z):
    """Returns (block_id, y) for the highest drawable (non-ID-0) block."""
    heightmap = prepared["heightmap"]

    if heightmap is None:
        # No heightmap: scan the column manually
        for y in range(319, WORLD_MIN_Y - 1, -1):
            block_id = get_block_id(prepared, local_x, y, local_z)
            if block_id != 0:
                return block_id, y
        return 0, None

    stored_value = unpack_heightmap(heightmap, local_x, local_z)
    if stored_value <= 0:
        return 0, None

    for y in range(stored_value + WORLD_MIN_Y - 1, WORLD_MIN_Y - 1, -1):
        block_id = get_block_id(prepared, local_x, y, local_z)
        if block_id != 0:
            return block_id, y

    return 0, None


def get_top_at(x, z):
    """(block_id, height) for a world column, or (0, None) if ungenerated."""
    prepared, lx, lz = get_prepared_chunk(x, z)
    if prepared is None:
        return 0, None
    return get_top_block(prepared, lx, lz)


def get_water_depth(prepared, local_x, local_z, water_y):
    """Depth to the lowest non-water opaque block."""
    for y in range(water_y - 1, WORLD_MIN_Y - 1, -1):
        block_id = get_block_id(prepared, local_x, y, local_z)
        if block_id != 0 and block_id != WATER_BLOCKS:
            return water_y - y
    return 10


def water_shade(depth, checker):
    if depth <= 2:
        return 2
    if depth <= 4:
        return 2 if checker else 1
    if depth <= 6:
        return 1
    if depth <= 9:
        return 1 if checker else 0
    return 0


def get_pixel_color(x, z, top, north_top):
    """top / north_top are (block_id, height) pairs for this column and the
    one to the north, handed in by render() so each column's chunk data is
    only ever looked up once."""
    block_id, height = top
    if height is None:
        return VOID_COLOR

    if block_id == WATER_BLOCKS:
        prepared, lx, lz = get_prepared_chunk(x, z)
        depth = get_water_depth(prepared, lx, lz, height)
        shade = water_shade(depth, (x + z) % 2 == 0)
        return (*COLORS[block_id][shade], 255)

    north_height = north_top[1]
    if north_height is None:
        shade = 1
    elif height < north_height:
        shade = 0
    elif height == north_height:
        shade = 1
    else:
        shade = 2

    return (*COLORS[block_id][shade], 255)


def render(world_path, top_left, bottom_right, output, progress_callback=None):
    """Render a heightmap for the rectangle [top_left, bottom_right).

    top_left is inclusive, bottom_right is exclusive, so top_left=(-16, -16)
    and bottom_right=(16, 16) produces a 32x32 image.

    Returns the number of distinct unknown block types encountered.
    """
    global WORLD_PATH
    WORLD_PATH = Path(world_path)

    UNKNOWN_BLOCKS.clear()
    REGION_CACHE.clear()
    CHUNK_CACHE.clear()

    min_x, min_z = top_left
    max_x, max_z = bottom_right

    if max_x <= min_x or max_z <= min_z:
        raise ValueError(
            "Bottom-right corner must be strictly greater than top-left "
            f"corner in both axes (got top_left={top_left}, "
            f"bottom_right={bottom_right})."
        )

    xs = range(min_x, max_x)
    width, height = len(xs), max_z - min_z

    img_array = np.zeros((height, width, 4), dtype=np.uint8)  # RGBA

    # We only need the height of the block North for shading
    prev_row = [get_top_at(x, min_z - 1) for x in xs]

    for z in range(min_z, max_z):
        print(f"Rendering row {z}/{max_z - 1}")

        if progress_callback:
            progress_callback(
                z - min_z + 1,
                height,
            )

        curr_row = [get_top_at(x, z) for x in xs]
        img_array[z - min_z] = [
            get_pixel_color(x, z, top, north)
            for x, top, north in zip(xs, curr_row, prev_row)
        ]
        prev_row = curr_row

        if z % 16 == 15:  # crossed a chunk boundary
            _prune(CHUNK_CACHE, lambda k: k[1], z // 16, CHUNK_ROWS_TO_KEEP)

        if z % 512 == 511:  # crossed a region boundary
            _prune(REGION_CACHE, lambda k: k[1], z >> 9, REGION_ROWS_TO_KEEP)

    if UNKNOWN_BLOCKS:
        print("\nUnknown blocks found:")
        for block in sorted(UNKNOWN_BLOCKS):
            print(" ", block)
    else:
        print("\nNo unknown blocks found.")

    img = Image.fromarray(img_array, mode="RGBA")
    print("Image size:", img.size)
    img.save(output)
    print("Saved:", output)

    return len(UNKNOWN_BLOCKS)


# I'm such a good programmer

if __name__ == "__main__":
    render()