"""
Builds the master fox atlas.

The original generated sheets are 4x4/16-frame animations. The app now plays
24-frame actions, so this builder can either stitch the original generated
sheets or expand the current atlas in place with subtle motion variants.
"""
import math
import os
from collections import deque
from pathlib import Path
from PIL import Image, ImageDraw

CELL_W = 192
CELL_H = 208
COLS = 8
SOURCE_FRAMES = 16
FRAMES_PER_ACTION = 24
ROWS_PER_ACTION = math.ceil(FRAMES_PER_ACTION / COLS)

ACTION_NAMES = [
    "sleep",
    "alert",
    "chore",
    "crying",
    "loving",
    "pawing",
    "walk",
    "shy",
    "cheering",
    "cuddling",
    "standing",
    "thinking",
]

ROWS = ROWS_PER_ACTION * len(ACTION_NAMES)
WIDTH = COLS * CELL_W
HEIGHT = ROWS * CELL_H

WORKSPACE_DIR = Path(__file__).resolve().parent
# Source of the original 4x4 generated sheets. Set FOXY_ATLAS_SRC to point at them;
# if unset (or the path is absent) the builder falls back to expanding the current
# atlas in place — so no developer-specific absolute path is committed.
ARTIFACT_DIR = Path(os.environ.get("FOXY_ATLAS_SRC") or (WORKSPACE_DIR / "_atlas_src")).expanduser()
OUTPUT = WORKSPACE_DIR / "ultimate_fox_spritesheet.png"

# Map the 4x4 generated sheets to (StartRow, EndRow) in the final atlas
SHEET_MAPPING = [
    ("sleep", ARTIFACT_DIR / "fox_sleep_cycle_1781821528123.png"),
    ("alert", ARTIFACT_DIR / "fox_alert_remind_1781821448738.png"),
    ("chore", ARTIFACT_DIR / "fox_sweep_chore_1781821489009.png"),
    ("crying", ARTIFACT_DIR / "fox_crying_1781821569799.png"),
    ("loving", ARTIFACT_DIR / "fox_loving_1781821609501.png"),
    ("pawing", ARTIFACT_DIR / "fox_pawing_1781821650068.png"),
    ("walk", ARTIFACT_DIR / "fox_walk_cycle_1781821406249.png"),
    ("shy", ARTIFACT_DIR / "fox_shy_1781823619478.png"),
    ("cheering", ARTIFACT_DIR / "fox_cheering_1781823657188.png"),
    ("cuddling", ARTIFACT_DIR / "fox_cuddling_1781823668752.png"),
    ("standing", ARTIFACT_DIR / "fox_standing_1781823679524.png"),
    ("thinking", ARTIFACT_DIR / "fox_thinking_1781824129178.png"),
]

def remove_checkerboard(img: Image.Image) -> Image.Image:
    """Remove edge-connected white/gray checkerboard backgrounds."""
    img = img.convert("RGBA")
    w, h = img.size
    pixels = img.load()
    
    def is_bg(x, y):
        r, g, b, a = pixels[x, y]
        if a == 0: return True
        if r > 168 and g > 168 and b > 168 and abs(r - g) < 28 and abs(g - b) < 28:
            return True
        return False

    visited = set()
    queue = deque()
    
    for x in range(w):
        queue.append((x, 0))
        queue.append((x, h - 1))
    for y in range(h):
        queue.append((0, y))
        queue.append((w - 1, y))
        
    while queue:
        x, y = queue.popleft()
        if (x, y) in visited: continue
        visited.add((x, y))
        
        if is_bg(x, y):
            pixels[x, y] = (0, 0, 0, 0)
            for dx, dy in [(0,1), (1,0), (0,-1), (-1,0), (1,1), (-1,-1), (1,-1), (-1,1)]:
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in visited:
                    queue.append((nx, ny))

    # Clean up thin 1px stray near-white pixels that survived the flood
    # fill only because they were diagonally (not orthogonally) connected
    # to the background. We only strip a pixel here if most of its
    # neighbors are ALREADY transparent — i.e. it's an isolated remnant
    # at the edge, not part of the fox's actual white/cream fur.
    to_clear = []
    for x in range(w):
        for y in range(h):
            r, g, b, a = pixels[x, y]
            if a == 0:
                continue
            if not (r > 168 and g > 168 and b > 168 and abs(r - g) < 28 and abs(g - b) < 28):
                continue
            transparent_neighbors = 0
            for dx, dy in [(0, 1), (1, 0), (0, -1), (-1, 0)]:
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    if pixels[nx, ny][3] == 0:
                        transparent_neighbors += 1
                else:
                    transparent_neighbors += 1  # edge of canvas counts as transparent
            if transparent_neighbors >= 3:
                to_clear.append((x, y))
    for x, y in to_clear:
        pixels[x, y] = (0, 0, 0, 0)

    return img

def _fit_to_cell(frame: Image.Image, max_w: int = 160, max_h: int = 172) -> Image.Image:
    frame = remove_checkerboard(frame)
    bbox = frame.getbbox()
    if not bbox:
        return Image.new("RGBA", (CELL_W, CELL_H), (0, 0, 0, 0))

    frame = frame.crop(bbox)
    ratio = min(max_w / frame.width, max_h / frame.height, 1.0)
    new_w = max(1, int(frame.width * ratio))
    new_h = max(1, int(frame.height * ratio))
    if (new_w, new_h) != frame.size:
        frame = frame.resize((new_w, new_h), Image.Resampling.LANCZOS)

    cell = Image.new("RGBA", (CELL_W, CELL_H), (0, 0, 0, 0))
    paste_x = (CELL_W - frame.width) // 2
    paste_y = (CELL_H - frame.height) // 2 + 12
    cell.paste(frame, (paste_x, paste_y), frame)
    return cell

def _pixel_heart(draw: ImageDraw.ImageDraw, x: int, y: int, scale: int, fill: tuple[int, int, int, int]):
    blocks = [
        (1, 0), (3, 0),
        (0, 1), (1, 1), (2, 1), (3, 1), (4, 1),
        (0, 2), (1, 2), (2, 2), (3, 2), (4, 2),
        (1, 3), (2, 3), (3, 3),
        (2, 4),
    ]
    for bx, by in blocks:
        draw.rectangle(
            (x + bx * scale, y + by * scale, x + (bx + 1) * scale - 1, y + (by + 1) * scale - 1),
            fill=fill,
        )

def _sparkle(draw: ImageDraw.ImageDraw, x: int, y: int, color: tuple[int, int, int, int]):
    draw.rectangle((x, y + 3, x + 6, y + 5), fill=color)
    draw.rectangle((x + 2, y, x + 4, y + 8), fill=color)

def _motion_variant(frame: Image.Image, action: str, idx: int) -> Image.Image:
    """Add tiny deterministic offsets so expanded frames do not look frozen."""
    phase = (idx / FRAMES_PER_ACTION) * math.tau
    bbox = frame.getbbox()
    if not bbox:
        return frame

    sprite = frame.crop(bbox)
    x = (CELL_W - sprite.width) // 2
    y = (CELL_H - sprite.height) // 2 + 12
    dx = 0
    dy = 0

    if action in {"sleep", "standing", "thinking"}:
        dy = round(math.sin(phase) * 2)
    elif action in {"alert", "crying"}:
        dx = round(math.sin(phase * 3) * 2)
        dy = round(math.sin(phase * 2) * 1)
    elif action in {"chore", "pawing"}:
        dx = round(math.sin(phase * 2) * 3)
        dy = round(math.sin(phase) * 1)
    elif action == "walk":
        dy = -abs(round(math.sin(phase * 2) * 2))
    elif action in {"loving", "cuddling", "cheering"}:
        dy = -abs(round(math.sin(phase * 2) * 4))
    elif action == "shy":
        dx = round(math.sin(phase) * 1)
        dy = round(math.sin(phase) * 1)

    cell = Image.new("RGBA", (CELL_W, CELL_H), (0, 0, 0, 0))
    cell.paste(sprite, (x + dx, y + dy), sprite)
    draw = ImageDraw.Draw(cell)

    if action in {"loving", "cuddling"} and idx % 4 in {0, 1}:
        _pixel_heart(draw, 132 + (idx % 3) * 4, 38 - (idx % 2) * 3, 3, (255, 102, 150, 210))
    if action == "cheering" and idx % 3 == 0:
        _sparkle(draw, 44 + (idx % 4) * 18, 36 + (idx % 2) * 8, (255, 216, 92, 220))
    if action == "thinking" and idx % 6 in {0, 1, 2}:
        for dot in range(3):
            size = 3 + dot
            px = 128 + dot * 12
            py = 44 - dot * 8 + (idx % 2)
            draw.rectangle((px, py, px + size, py + size), fill=(95, 85, 120, 180))

    return cell

def expand_frames(frames: list[Image.Image], action: str) -> list[Image.Image]:
    if not frames:
        return []
    normalized = [_fit_to_cell(frame) for frame in frames]
    expanded = []
    for i in range(FRAMES_PER_ACTION):
        source_idx = round(i * (len(normalized) - 1) / max(1, FRAMES_PER_ACTION - 1))
        expanded.append(_motion_variant(normalized[source_idx], action, i))
    return expanded

def extract_16_frames(sheet_path: Path) -> list[Image.Image]:
    """Slices a 4x4 image into 16 individual frame images."""
    try:
        img = Image.open(sheet_path)
    except FileNotFoundError:
        print(f"Skipping {sheet_path} (not found)")
        return []
        
    img = remove_checkerboard(img)
    w, h = img.size
    frame_w = w // 4
    frame_h = h // 4
    
    frames = []
    for row in range(4):
        for col in range(4):
            left = col * frame_w
            upper = row * frame_h
            right = left + frame_w
            lower = upper + frame_h
            frame = img.crop((left, upper, right, lower))
            
            frames.append(frame)
    return frames

def extract_existing_action_frames(action_index: int) -> list[Image.Image]:
    """Read frames for one action from the current atlas, supporting old or new layout."""
    if not OUTPUT.exists():
        return []

    img = Image.open(OUTPUT).convert("RGBA")
    rows = img.height // CELL_H
    rows_per_action = rows // len(ACTION_NAMES)
    if rows_per_action <= 0:
        return []

    available = min(rows_per_action * COLS, FRAMES_PER_ACTION)
    start_row = action_index * rows_per_action
    frames = []
    for i in range(available):
        row = start_row + (i // COLS)
        col = i % COLS
        box = (col * CELL_W, row * CELL_H, (col + 1) * CELL_W, (row + 1) * CELL_H)
        frames.append(img.crop(box))
    return frames

def build_atlas():
    atlas = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    
    for action_index, (action, path) in enumerate(SHEET_MAPPING):
        start_row = action_index * ROWS_PER_ACTION
        if path.exists():
            print(f"Processing {action}: {os.path.basename(path)} -> rows {start_row}-{start_row + ROWS_PER_ACTION - 1}")
            frames = extract_16_frames(path)
        else:
            print(f"Processing {action}: expanding existing atlas -> rows {start_row}-{start_row + ROWS_PER_ACTION - 1}")
            frames = extract_existing_action_frames(action_index)
        if not frames:
            continue

        frames = expand_frames(frames[:FRAMES_PER_ACTION], action)
        
        for i, frame in enumerate(frames):
            target_row = start_row + (i // COLS)
            target_col = i % 8
            paste_x = target_col * CELL_W
            paste_y = target_row * CELL_H
            atlas.paste(frame, (paste_x, paste_y), frame)
            
    atlas.save(OUTPUT)
    print(f"\nSaved ultimate_fox_spritesheet.png ({WIDTH}x{HEIGHT}) with {FRAMES_PER_ACTION} frames/action")

if __name__ == "__main__":
    build_atlas()
