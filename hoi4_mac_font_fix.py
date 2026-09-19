#!/usr/bin/env python3
"""Repair the macOS text rendering of a Hearts of Iron IV mod.

The script corrects two defects that make text render in the wrong position and
at the wrong size on macOS, but not on Windows.

Defect 1 - the text shader does not compile on macOS.
    A mod writes `all(a == b)`, where `a` and `b` are vectors. Windows uses
    Direct3D and HLSL. There, a comparison of two vectors gives a vector of
    booleans, so `all()` accepts it. macOS uses OpenGL and GLSL. There, the same
    comparison gives one boolean, so `all()` has no matching overload and the
    shader fails to link. The game then draws every string with a fallback
    program, which puts the text in the wrong place.
    The script rewrites each call as a comparison of one component at a time.
    That means the same thing and compiles on both platforms.

Defect 2 - a font atlas is smaller than its `.fnt` file declares.
    An image tool cropped the empty bottom of some atlas textures, but nobody
    updated the `scaleH` value in the matching `.fnt` file. Windows takes the
    real size of the texture, so the text stays correct. macOS takes the
    declared size, so it reads a small strip and stretches it.
    The script adds empty rows to the bottom of the texture until the real size
    is equal to the declared size. Each glyph keeps its original position.

The script is safe to run more than once. It changes nothing on the second run.

Usage:
    python3 hoi4_mac_font_fix.py                 # find mods, show a report
    python3 hoi4_mac_font_fix.py --apply         # write the corrections
    python3 hoi4_mac_font_fix.py --apply --path /path/to/mod
    python3 hoi4_mac_font_fix.py --restore --path /path/to/mod
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import struct
import sys

__version__ = "1.0.0"

BACKUP_DIR_NAME = ".mac_fix_backup"

# --- DDS header ---------------------------------------------------------------

DDS_MAGIC = b"DDS "
DDS_HEADER_SIZE = 128

OFF_FLAGS = 8
OFF_HEIGHT = 12
OFF_WIDTH = 16
OFF_LINEAR_SIZE = 20
OFF_MIPMAP_COUNT = 28
OFF_FOURCC = 84
OFF_CAPS = 108

DDSD_MIPMAPCOUNT = 0x20000
DDSD_LINEARSIZE = 0x80000
DDSCAPS_COMPLEX = 0x8
DDSCAPS_MIPMAP = 0x400000

# Bytes for one 4x4 block of each compressed format that a font atlas uses.
BLOCK_BYTES = {
    b"DXT1": 8,
    b"DXT2": 16,
    b"DXT3": 16,
    b"DXT4": 16,
    b"DXT5": 16,
    b"ATI1": 8,
    b"ATI2": 16,
    b"BC4U": 8,
    b"BC5U": 16,
}


class DdsError(Exception):
    """The script cannot read or correct a DDS file."""


def parse_dds_header(raw: bytes) -> dict:
    """Return the fields of a DDS header that the script needs.

    Raise DdsError if the data is not a DDS file that the script understands.
    """
    if len(raw) < DDS_HEADER_SIZE:
        raise DdsError("file is shorter than a DDS header")
    if raw[:4] != DDS_MAGIC:
        raise DdsError("file does not start with the DDS marker")
    height, width = struct.unpack_from("<II", raw, OFF_HEIGHT)
    (mipmaps,) = struct.unpack_from("<I", raw, OFF_MIPMAP_COUNT)
    fourcc = raw[OFF_FOURCC:OFF_FOURCC + 4]
    return {
        "width": width,
        "height": height,
        "mipmaps": mipmaps,
        "fourcc": fourcc,
        "compressed": fourcc in BLOCK_BYTES,
    }


def top_level_size(width: int, height: int, fourcc: bytes) -> int:
    """Return the byte count of mip level 0 for a compressed texture."""
    block = BLOCK_BYTES[fourcc]
    return ((width + 3) // 4) * ((height + 3) // 4) * block


def repad_dds(raw: bytes, target_width: int, target_height: int) -> bytes:
    """Return the DDS data grown to the target size, with no mip chain.

    The function keeps mip level 0 and adds empty blocks below it. An empty
    block of every supported format is 16 zero bytes or 8 zero bytes, which
    decodes to a fully transparent 4x4 square.

    Raise DdsError if the file cannot grow this way.
    """
    info = parse_dds_header(raw)
    if not info["compressed"]:
        raise DdsError("format %r is not a block compressed format"
                       % info["fourcc"].decode("latin-1"))
    if info["width"] != target_width:
        raise DdsError("width is %d but the font file declares %d"
                       % (info["width"], target_width))
    if info["height"] > target_height:
        raise DdsError("height is %d, which is larger than the declared %d"
                       % (info["height"], target_height))

    fourcc = info["fourcc"]
    block = BLOCK_BYTES[fourcc]
    keep = top_level_size(info["width"], info["height"], fourcc)
    body = raw[DDS_HEADER_SIZE:DDS_HEADER_SIZE + keep]
    if len(body) < keep:
        raise DdsError("file is truncated: mip level 0 needs %d bytes, "
                       "the file holds %d" % (keep, len(body)))

    blocks_wide = (target_width + 3) // 4
    rows_now = (info["height"] + 3) // 4
    rows_target = (target_height + 3) // 4
    body += b"\x00" * ((rows_target - rows_now) * blocks_wide * block)

    header = bytearray(raw[:DDS_HEADER_SIZE])
    (flags,) = struct.unpack_from("<I", header, OFF_FLAGS)
    struct.pack_into("<I", header, OFF_FLAGS,
                     (flags & ~DDSD_MIPMAPCOUNT) | DDSD_LINEARSIZE)
    struct.pack_into("<I", header, OFF_HEIGHT, target_height)
    struct.pack_into("<I", header, OFF_WIDTH, target_width)
    struct.pack_into("<I", header, OFF_LINEAR_SIZE, blocks_wide * rows_target * block)
    struct.pack_into("<I", header, OFF_MIPMAP_COUNT, 0)
    (caps,) = struct.unpack_from("<I", header, OFF_CAPS)
    struct.pack_into("<I", header, OFF_CAPS,
                     caps & ~(DDSCAPS_COMPLEX | DDSCAPS_MIPMAP))
    return bytes(header) + body


# --- BMFont .fnt file ---------------------------------------------------------

_SCALE_RE = re.compile(rb"scaleW=(\d+)\s+scaleH=(\d+)")
_CHAR_RE = re.compile(rb"char id=\d+\s+x=\d+\s+y=(\d+)\s+width=\d+\s+height=(\d+)")


def parse_fnt_scale(raw: bytes) -> tuple[int, int] | None:
    """Return the (width, height) that a .fnt file declares for its texture.

    Return None when the file holds no `scaleW`/`scaleH` pair.
    """
    match = _SCALE_RE.search(raw)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def lowest_glyph_edge(raw: bytes) -> int:
    """Return the largest `y + height` over every glyph in a .fnt file.

    A crop is safe to undo only when this value fits inside the real texture,
    because that proves the tool removed empty rows from the bottom alone.
    """
    lowest = 0
    for match in _CHAR_RE.finditer(raw):
        lowest = max(lowest, int(match.group(1)) + int(match.group(2)))
    return lowest


# --- Shader source ------------------------------------------------------------

_COMPONENTS = "rgba"
_SCALAR_RE = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)f?$")
_VECTOR_CTOR_RE = re.compile(r"^(?:float|half)([234])\s*\((.*)\)$", re.DOTALL)
_SWIZZLE_RE = re.compile(r"\.([rgba]{2,4}|[xyzw]{2,4})$")


def _split_top_level(text: str, separator: str) -> list[str]:
    """Split text on a separator that sits outside every pair of brackets."""
    parts, depth, start, index = [], 0, 0, 0
    while index < len(text):
        char = text[index]
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif depth == 0 and text.startswith(separator, index):
            parts.append(text[start:index])
            index += len(separator)
            start = index
            continue
        index += 1
    parts.append(text[start:])
    return parts


def _find_call(text: str, name: str, start: int) -> tuple[int, int, str] | None:
    """Find the next `name(...)` call and return its span and its argument."""
    pattern = re.compile(r"\b%s\s*\(" % re.escape(name))
    match = pattern.search(text, start)
    if match is None:
        return None
    depth, index = 1, match.end()
    while index < len(text) and depth:
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
        index += 1
    if depth:
        return None
    return match.start(), index, text[match.end():index - 1]


def _component_count(left: str, right: str) -> int:
    """Return how many components the two sides of a comparison hold."""
    for side in (left, right):
        swizzle = _SWIZZLE_RE.search(side)
        if swizzle:
            return len(swizzle.group(1))
        ctor = _VECTOR_CTOR_RE.match(side)
        if ctor:
            return int(ctor.group(1))
    return 4


def _side_terms(side: str, count: int) -> list[str] | None:
    """Return the per-component text of one side of a comparison.

    Return None when the side is not a shape the script can rewrite.
    """
    side = side.strip()
    if _SCALAR_RE.match(side):
        return [side] * count
    ctor = _VECTOR_CTOR_RE.match(side)
    if ctor:
        args = [part.strip() for part in _split_top_level(ctor.group(2), ",")]
        if len(args) == count:
            return args
        if len(args) == 1 and _SCALAR_RE.match(args[0]):
            return args * count
        return None
    swizzle = _SWIZZLE_RE.search(side)
    if swizzle:
        base = side[:swizzle.start()]
        letters = swizzle.group(1)
        if len(letters) != count:
            return None
        return ["%s.%s" % (base, letter) for letter in letters]
    if re.match(r"^[A-Za-z_][\w.]*$", side):
        return ["%s.%s" % (side, _COMPONENTS[i]) for i in range(count)]
    return None


def patch_shader_source(text: str) -> tuple[str, int]:
    """Rewrite every `all`/`any` call on a vector comparison.

    Return the new text and the number of calls rewritten. A call the script
    cannot read stays as it is, so the function never corrupts a shader.
    """
    changes = 0
    for name, joiner in (("all", " && "), ("any", " || ")):
        position = 0
        while True:
            found = _find_call(text, name, position)
            if found is None:
                break
            start, end, argument = found
            sides = _split_top_level(argument, "==")
            if len(sides) != 2:
                position = end
                continue
            count = _component_count(sides[0].strip(), sides[1].strip())
            left = _side_terms(sides[0], count)
            right = _side_terms(sides[1], count)
            if left is None or right is None:
                position = end
                continue
            body = joiner.join(
                "%s == %s" % (left[i], right[i]) for i in range(count))
            replacement = "(%s)" % body
            text = text[:start] + replacement + text[end:]
            position = start + len(replacement)
            changes += 1
    return text, changes


# --- Locating a mod -----------------------------------------------------------

def default_search_roots() -> list[str]:
    """Return the usual places a Hearts of Iron IV mod lives on macOS."""
    home = os.path.expanduser("~")
    return [
        os.path.join(home, "Library", "Application Support", "Steam",
                     "steamapps", "workshop", "content", "394360"),
        os.path.join(home, "Documents", "Paradox Interactive",
                     "Hearts of Iron IV", "mod"),
    ]


def find_mods(roots: list[str]) -> list[str]:
    """Return every directory under the roots that looks like a mod."""
    found = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if os.path.isdir(path) and os.path.isdir(os.path.join(path, "gfx")):
                found.append(path)
    return found


# --- The two repairs ----------------------------------------------------------

def _backup(mod_root: str, path: str, apply_changes: bool) -> None:
    """Copy a file into the backup folder, unless a copy is already there."""
    relative = os.path.relpath(path, mod_root)
    target = os.path.join(mod_root, BACKUP_DIR_NAME, relative)
    if os.path.exists(target):
        return
    if apply_changes:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(path, target)


def fix_shaders(mod_root: str, apply_changes: bool) -> list[str]:
    """Correct every shader in the mod. Return one report line per file."""
    report = []
    fx_dir = os.path.join(mod_root, "gfx", "FX")
    if not os.path.isdir(fx_dir):
        return report
    for name in sorted(os.listdir(fx_dir)):
        if not name.endswith((".shader", ".fxh")):
            continue
        path = os.path.join(fx_dir, name)
        raw = open(path, "rb").read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            report.append("  skip  gfx/FX/%s (not UTF-8 text)" % name)
            continue
        new_text, changes = patch_shader_source(text)
        if not changes:
            continue
        report.append("  fix   gfx/FX/%s (%d vector comparison%s)"
                      % (name, changes, "" if changes == 1 else "s"))
        _backup(mod_root, path, apply_changes)
        if apply_changes:
            # Write bytes, so the original CRLF line endings survive.
            open(path, "wb").write(new_text.encode("utf-8"))
    return report


def fix_font_atlases(mod_root: str, apply_changes: bool) -> list[str]:
    """Correct every cropped font atlas. Return one report line per file."""
    report = []
    fonts_dir = os.path.join(mod_root, "gfx", "fonts")
    if not os.path.isdir(fonts_dir):
        return report
    for directory, _subdirs, files in sorted(os.walk(fonts_dir)):
        for name in sorted(files):
            if not name.endswith(".fnt"):
                continue
            fnt_path = os.path.join(directory, name)
            dds_path = fnt_path[:-4] + ".dds"
            if not os.path.exists(dds_path):
                continue
            fnt_raw = open(fnt_path, "rb").read()
            declared = parse_fnt_scale(fnt_raw)
            if declared is None:
                continue
            width, height = declared
            dds_raw = open(dds_path, "rb").read()
            try:
                info = parse_dds_header(dds_raw)
            except DdsError as error:
                report.append("  skip  %s (%s)"
                              % (os.path.relpath(dds_path, mod_root), error))
                continue
            if (info["width"], info["height"]) == (width, height):
                continue
            relative = os.path.relpath(dds_path, mod_root)
            lowest = lowest_glyph_edge(fnt_raw)
            if lowest > info["height"]:
                report.append("  skip  %s (a glyph reaches row %d but the "
                              "texture is %d tall, so the crop removed real "
                              "pixels)" % (relative, lowest, info["height"]))
                continue
            try:
                new_raw = repad_dds(dds_raw, width, height)
            except DdsError as error:
                report.append("  skip  %s (%s)" % (relative, error))
                continue
            report.append("  fix   %s (%dx%d -> %dx%d)"
                          % (relative, info["width"], info["height"],
                             width, height))
            _backup(mod_root, dds_path, apply_changes)
            if apply_changes:
                open(dds_path, "wb").write(new_raw)
    return report


def restore(mod_root: str) -> list[str]:
    """Put every backed up file back. Return one report line per file."""
    backup_root = os.path.join(mod_root, BACKUP_DIR_NAME)
    if not os.path.isdir(backup_root):
        return ["  nothing to restore: no %s folder" % BACKUP_DIR_NAME]
    report = []
    for directory, _subdirs, files in sorted(os.walk(backup_root)):
        for name in sorted(files):
            source = os.path.join(directory, name)
            relative = os.path.relpath(source, backup_root)
            shutil.copy2(source, os.path.join(mod_root, relative))
            report.append("  back  %s" % relative)
    shutil.rmtree(backup_root)
    return report


def process(mod_root: str, apply_changes: bool) -> list[str]:
    """Run both repairs on one mod. Return the full report."""
    return fix_shaders(mod_root, apply_changes) + \
        fix_font_atlases(mod_root, apply_changes)


# --- Command line -------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hoi4_mac_font_fix.py",
        description="Repair the macOS text rendering of a Hearts of Iron IV mod.")
    parser.add_argument("--path", action="append", default=[], metavar="DIR",
                        help="the mod folder to repair; repeat for more than one")
    parser.add_argument("--apply", action="store_true",
                        help="write the corrections; without it the script only reports")
    parser.add_argument("--restore", action="store_true",
                        help="put the original files back")
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    mods = [os.path.abspath(p) for p in args.path]
    if not mods:
        mods = find_mods(default_search_roots())
        if not mods:
            print("No mod found. Give the folder with --path /path/to/mod.")
            return 1
        print("Found %d mod folder%s."
              % (len(mods), "" if len(mods) == 1 else "s"))

    total = 0
    for mod_root in mods:
        if not os.path.isdir(mod_root):
            print("\n%s\n  not a folder" % mod_root)
            continue
        print("\n%s" % mod_root)
        lines = restore(mod_root) if args.restore \
            else process(mod_root, args.apply)
        if not lines:
            print("  nothing to correct")
            continue
        total += len(lines)
        for line in lines:
            print(line)

    if args.restore:
        print("\nRestored.")
    elif not args.apply:
        print("\nThis was a report only. Add --apply to write %d change%s."
              % (total, "" if total == 1 else "s"))
    else:
        print("\nDone. Originals are in each mod's %s folder. "
              "Run with --restore to undo." % BACKUP_DIR_NAME)
        print("Steam replaces a Workshop mod on its next update, which removes "
              "these corrections. Run the script again after an update.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
