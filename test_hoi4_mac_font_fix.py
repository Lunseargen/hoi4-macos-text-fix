"""Tests for hoi4_mac_font_fix."""

from __future__ import annotations

import os
import struct

import pytest

import hoi4_mac_font_fix as fix


# --- helpers ------------------------------------------------------------------

def make_dds(width: int, height: int, fourcc: bytes = b"DXT5",
             mipmaps: int = 0, body: bytes | None = None,
             magic: bytes = b"DDS ", caps: int = 0x1000,
             flags: int = 0x1007) -> bytes:
    """Build a DDS file with a valid header and a filled mip level 0."""
    header = bytearray(128)
    header[0:4] = magic
    struct.pack_into("<I", header, 4, 124)
    struct.pack_into("<I", header, fix.OFF_FLAGS, flags)
    struct.pack_into("<I", header, fix.OFF_HEIGHT, height)
    struct.pack_into("<I", header, fix.OFF_WIDTH, width)
    struct.pack_into("<I", header, fix.OFF_MIPMAP_COUNT, mipmaps)
    header[fix.OFF_FOURCC:fix.OFF_FOURCC + 4] = fourcc
    struct.pack_into("<I", header, fix.OFF_CAPS, caps)
    if body is None:
        size = fix.top_level_size(width, height, fourcc) \
            if fourcc in fix.BLOCK_BYTES else width * height * 4
        body = bytes(range(256)) * (size // 256 + 1)
        body = body[:size]
    return bytes(header) + body


def make_fnt(width: int, height: int, glyphs=((0, 10),)) -> bytes:
    """Build a small BMFont text file."""
    lines = ['info face="T" size=-18',
             "common lineHeight=22 base=18 scaleW=%d scaleH=%d pages=1"
             % (width, height)]
    for index, (y, glyph_height) in enumerate(glyphs):
        lines.append("char id=%d   x=0   y=%d   width=5     height=%d"
                     % (32 + index, y, glyph_height))
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def make_mod(root, shader: str | None = None, fonts=()):
    """Build a mod tree on disk and return its path."""
    os.makedirs(os.path.join(root, "gfx", "FX"), exist_ok=True)
    os.makedirs(os.path.join(root, "gfx", "fonts"), exist_ok=True)
    if shader is not None:
        with open(os.path.join(root, "gfx", "FX", "text.shader"), "wb") as handle:
            handle.write(shader.encode("utf-8"))
    for name, fnt, dds in fonts:
        base = os.path.join(root, "gfx", "fonts", name)
        with open(base + ".fnt", "wb") as handle:
            handle.write(fnt)
        if dds is not None:
            with open(base + ".dds", "wb") as handle:
                handle.write(dds)
    return root


# --- parse_dds_header ---------------------------------------------------------

def test_parse_dds_header_reads_the_fields():
    info = fix.parse_dds_header(make_dds(2048, 4096, b"DXT5", mipmaps=3))
    assert info == {"width": 2048, "height": 4096, "mipmaps": 3,
                    "fourcc": b"DXT5", "compressed": True}


def test_parse_dds_header_marks_an_unknown_format_as_uncompressed():
    assert fix.parse_dds_header(make_dds(4, 4, b"\x00\x00\x00\x00"))["compressed"] is False


def test_parse_dds_header_rejects_a_short_file():
    with pytest.raises(fix.DdsError, match="shorter than"):
        fix.parse_dds_header(b"DDS " + bytes(10))


def test_parse_dds_header_rejects_a_wrong_marker():
    with pytest.raises(fix.DdsError, match="DDS marker"):
        fix.parse_dds_header(make_dds(4, 4, magic=b"XXXX"))


# --- top_level_size -----------------------------------------------------------

@pytest.mark.parametrize("width,height,fourcc,expected", [
    (2048, 4096, b"DXT5", 512 * 1024 * 16),
    (2048, 4096, b"DXT1", 512 * 1024 * 8),
    (2048, 2391, b"DXT5", 512 * 598 * 16),   # a height that 4 does not divide
    (1, 1, b"DXT5", 16),
])
def test_top_level_size(width, height, fourcc, expected):
    assert fix.top_level_size(width, height, fourcc) == expected


# --- repad_dds ----------------------------------------------------------------

def test_repad_dds_grows_the_texture_and_keeps_the_pixels():
    raw = make_dds(2048, 208, b"DXT5", mipmaps=12)
    kept = raw[128:128 + fix.top_level_size(2048, 208, b"DXT5")]

    out = fix.repad_dds(raw, 2048, 4096)

    info = fix.parse_dds_header(out)
    assert (info["width"], info["height"], info["mipmaps"]) == (2048, 4096, 0)
    assert len(out) == 128 + fix.top_level_size(2048, 4096, b"DXT5")
    assert out[128:128 + len(kept)] == kept          # every glyph stays put
    assert set(out[128 + len(kept):]) == {0}         # the rest is transparent


def test_repad_dds_clears_the_mipmap_header_flags():
    raw = make_dds(2048, 208, b"DXT5", mipmaps=12,
                   caps=0x1000 | fix.DDSCAPS_COMPLEX | fix.DDSCAPS_MIPMAP,
                   flags=0x1007 | fix.DDSD_MIPMAPCOUNT)

    out = fix.repad_dds(raw, 2048, 4096)

    (flags,) = struct.unpack_from("<I", out, fix.OFF_FLAGS)
    (caps,) = struct.unpack_from("<I", out, fix.OFF_CAPS)
    (linear,) = struct.unpack_from("<I", out, fix.OFF_LINEAR_SIZE)
    assert not flags & fix.DDSD_MIPMAPCOUNT
    assert flags & fix.DDSD_LINEARSIZE
    assert not caps & (fix.DDSCAPS_COMPLEX | fix.DDSCAPS_MIPMAP)
    assert linear == fix.top_level_size(2048, 4096, b"DXT5")


def test_repad_dds_handles_dxt1():
    out = fix.repad_dds(make_dds(64, 16, b"DXT1"), 64, 64)
    assert len(out) == 128 + fix.top_level_size(64, 64, b"DXT1")


def test_repad_dds_accepts_a_height_that_four_does_not_divide():
    out = fix.repad_dds(make_dds(2048, 2391, b"DXT5", mipmaps=12), 2048, 4096)
    assert fix.parse_dds_header(out)["height"] == 4096


def test_repad_dds_keeps_a_texture_that_is_already_the_right_size():
    raw = make_dds(64, 64, b"DXT5")
    out = fix.repad_dds(raw, 64, 64)
    assert out[128:] == raw[128:]


def test_repad_dds_rejects_an_uncompressed_format():
    with pytest.raises(fix.DdsError, match="not a block compressed"):
        fix.repad_dds(make_dds(4, 4, b"\x00\x00\x00\x00"), 4, 8)


def test_repad_dds_rejects_a_different_width():
    with pytest.raises(fix.DdsError, match="width is 64"):
        fix.repad_dds(make_dds(64, 16, b"DXT5"), 128, 64)


def test_repad_dds_rejects_a_texture_that_is_too_tall():
    with pytest.raises(fix.DdsError, match="larger than"):
        fix.repad_dds(make_dds(64, 128, b"DXT5"), 64, 64)


def test_repad_dds_rejects_a_truncated_file():
    raw = make_dds(64, 64, b"DXT5")[:-16]
    with pytest.raises(fix.DdsError, match="truncated"):
        fix.repad_dds(raw, 64, 128)


# --- parse_fnt_scale and lowest_glyph_edge ------------------------------------

def test_parse_fnt_scale_reads_the_pair():
    assert fix.parse_fnt_scale(make_fnt(2048, 4096)) == (2048, 4096)


def test_parse_fnt_scale_returns_none_without_a_pair():
    assert fix.parse_fnt_scale(b"info face=\"T\"\r\n") is None


def test_lowest_glyph_edge_returns_the_largest_bottom():
    assert fix.lowest_glyph_edge(make_fnt(64, 64, [(0, 10), (90, 18), (40, 7)])) == 108


def test_lowest_glyph_edge_returns_zero_without_glyphs():
    assert fix.lowest_glyph_edge(b"common scaleW=1 scaleH=1\r\n") == 0


# --- patch_shader_source ------------------------------------------------------

def test_patch_shader_rewrites_the_real_mod_line():
    text, count = fix.patch_shader_source(
        "if(all(v.vColor == float4(255.f/255.f, 60.f/255.f, 0.f/255.f, 1.f)))return x;")
    assert count == 1
    assert text == ("if((v.vColor.r == 255.f/255.f && v.vColor.g == 60.f/255.f"
                    " && v.vColor.b == 0.f/255.f && v.vColor.a == 1.f))return x;")


def test_patch_shader_rewrites_a_swizzle_against_a_scalar():
    text, count = fix.patch_shader_source("if(all(OutColor.rgba == 0.f))discard;")
    assert count == 1
    assert text == ("if((OutColor.r == 0.f && OutColor.g == 0.f && "
                    "OutColor.b == 0.f && OutColor.a == 0.f))discard;")


def test_patch_shader_uses_or_for_any():
    text, count = fix.patch_shader_source("any(a.rg == b.rg)")
    assert count == 1
    assert text == "(a.r == b.r || a.g == b.g)"


def test_patch_shader_handles_two_component_vectors():
    text, _ = fix.patch_shader_source("all(v.xy == float2(1.f, 2.f))")
    assert text == "(v.x == 1.f && v.y == 2.f)"


def test_patch_shader_handles_a_bare_name_on_both_sides():
    text, _ = fix.patch_shader_source("all(a == b)")
    assert text == ("(a.r == b.r && a.g == b.g && a.b == b.b && a.a == b.a)")


def test_patch_shader_rewrites_every_call_in_a_file():
    source = "\r\n".join(
        "if(all(v.vColor == float4(%d.f, 0.f, 0.f, 1.f)))return;" % n
        for n in range(7))
    text, count = fix.patch_shader_source(source)
    assert count == 7
    assert "all(" not in text
    assert text.count("\r\n") == 6          # line endings survive


def test_patch_shader_is_idempotent():
    once, first = fix.patch_shader_source("all(v.vColor == float4(1.f,0.f,0.f,1.f))")
    twice, second = fix.patch_shader_source(once)
    assert (first, second) == (1, 0)
    assert twice == once


def test_patch_shader_leaves_a_scalar_call_alone():
    source = "if(all(flag))return;"
    assert fix.patch_shader_source(source) == (source, 0)


def test_patch_shader_leaves_a_call_it_cannot_read_alone():
    source = "if(all(f(x) == g(y)))return;"
    assert fix.patch_shader_source(source) == (source, 0)


def test_patch_shader_leaves_a_mismatched_component_count_alone():
    source = "if(all(v.rgba == float2(1.f, 2.f)))return;"
    assert fix.patch_shader_source(source) == (source, 0)


def test_patch_shader_leaves_a_wrong_length_constructor_alone():
    source = "if(all(v.rgba == float4(1.f, 2.f)))return;"
    assert fix.patch_shader_source(source) == (source, 0)


def test_patch_shader_spreads_a_one_argument_constructor():
    text, _ = fix.patch_shader_source("all(v.rgba == float4(0.f))")
    assert text == ("(v.r == 0.f && v.g == 0.f && v.b == 0.f && v.a == 0.f)")


def test_patch_shader_handles_a_comparison_inside_nested_calls():
    text, count = fix.patch_shader_source("all(saturate(v).rgba == float4(1.f))")
    assert count == 1
    assert text.startswith("(saturate(v).r == 1.f")


def test_patch_shader_leaves_an_unbalanced_call_alone():
    source = "if(all(v.rgba == float4(1.f,1.f,1.f,1.f)"
    assert fix.patch_shader_source(source) == (source, 0)


def test_patch_shader_leaves_a_call_without_a_comparison_alone():
    source = "all(v.rgba)"
    assert fix.patch_shader_source(source) == (source, 0)


def test_patch_shader_returns_unchanged_text_when_there_is_nothing_to_do():
    assert fix.patch_shader_source("float4 main(){return 0;}") == (
        "float4 main(){return 0;}", 0)


# --- _split_top_level ---------------------------------------------------------

def test_split_top_level_ignores_a_separator_inside_brackets():
    assert fix._split_top_level("a(1,2),b[3,4],c", ",") == ["a(1,2)", "b[3,4]", "c"]


# --- finding mods -------------------------------------------------------------

def test_default_search_roots_names_both_mod_locations():
    roots = fix.default_search_roots()
    assert any("workshop" in root for root in roots)
    assert any("Paradox Interactive" in root for root in roots)


def test_find_mods_returns_only_folders_that_hold_gfx(tmp_path):
    root = tmp_path / "content"
    (root / "111" / "gfx").mkdir(parents=True)
    (root / "222").mkdir()
    (root / "notes.txt").write_text("x")
    assert fix.find_mods([str(root), str(tmp_path / "absent")]) == \
        [str(root / "111")]


# --- fix_shaders --------------------------------------------------------------

SHADER = "PixelShader{ if(all(v.vColor == float4(1.f,0.f,0.f,1.f)))return; }\r\n"


def test_fix_shaders_reports_without_writing(tmp_path):
    mod = make_mod(str(tmp_path / "mod"), shader=SHADER)
    report = fix.fix_shaders(mod, apply_changes=False)
    assert report == ["  fix   gfx/FX/text.shader (1 vector comparison)"]
    assert open(os.path.join(mod, "gfx", "FX", "text.shader"), "rb").read() == SHADER.encode("utf-8")
    assert not os.path.exists(os.path.join(mod, fix.BACKUP_DIR_NAME))


def test_fix_shaders_writes_and_keeps_crlf(tmp_path):
    mod = make_mod(str(tmp_path / "mod"), shader=SHADER)
    fix.fix_shaders(mod, apply_changes=True)
    raw = open(os.path.join(mod, "gfx", "FX", "text.shader"), "rb").read()
    assert b"all(" not in raw
    assert raw.endswith(b"\r\n")
    assert os.path.exists(
        os.path.join(mod, fix.BACKUP_DIR_NAME, "gfx", "FX", "text.shader"))


def test_fix_shaders_pluralises_the_count(tmp_path):
    mod = make_mod(str(tmp_path / "mod"), shader=SHADER + SHADER)
    assert "2 vector comparisons" in fix.fix_shaders(mod, False)[0]


def test_fix_shaders_is_idempotent(tmp_path):
    mod = make_mod(str(tmp_path / "mod"), shader=SHADER)
    fix.fix_shaders(mod, apply_changes=True)
    assert fix.fix_shaders(mod, apply_changes=True) == []


def test_fix_shaders_keeps_the_first_backup(tmp_path):
    mod = make_mod(str(tmp_path / "mod"), shader=SHADER)
    fix.fix_shaders(mod, apply_changes=True)
    backup = os.path.join(mod, fix.BACKUP_DIR_NAME, "gfx", "FX", "text.shader")
    original = open(backup, "rb").read()
    fix.fix_shaders(mod, apply_changes=True)
    assert open(backup, "rb").read() == original


def test_fix_shaders_skips_a_file_that_is_not_utf8(tmp_path):
    mod = make_mod(str(tmp_path / "mod"))
    with open(os.path.join(mod, "gfx", "FX", "bad.shader"), "wb") as handle:
        handle.write(b"\xff\xfe all(v.rgba == float4(1.f))")
    assert fix.fix_shaders(mod, False) == ["  skip  gfx/FX/bad.shader (not UTF-8 text)"]


def test_fix_shaders_ignores_an_unrelated_extension(tmp_path):
    mod = make_mod(str(tmp_path / "mod"))
    with open(os.path.join(mod, "gfx", "FX", "readme.txt"), "w") as handle:
        handle.write("all(v.rgba == float4(1.f))")
    assert fix.fix_shaders(mod, False) == []


def test_fix_shaders_returns_nothing_without_an_fx_folder(tmp_path):
    (tmp_path / "mod").mkdir()
    assert fix.fix_shaders(str(tmp_path / "mod"), False) == []


# --- fix_font_atlases ---------------------------------------------------------

def cropped_font(name="F", real_height=208, declared=(2048, 4096)):
    return (name, make_fnt(declared[0], declared[1], [(0, real_height)]),
            make_dds(declared[0], real_height, b"DXT5", mipmaps=12))


def test_fix_font_atlases_repads_a_cropped_texture(tmp_path):
    mod = make_mod(str(tmp_path / "mod"), fonts=[cropped_font()])
    report = fix.fix_font_atlases(mod, apply_changes=True)
    assert report == ["  fix   gfx/fonts/F.dds (2048x208 -> 2048x4096)"]
    info = fix.parse_dds_header(
        open(os.path.join(mod, "gfx", "fonts", "F.dds"), "rb").read())
    assert (info["height"], info["mipmaps"]) == (4096, 0)


def test_fix_font_atlases_reports_without_writing(tmp_path):
    mod = make_mod(str(tmp_path / "mod"), fonts=[cropped_font()])
    before = open(os.path.join(mod, "gfx", "fonts", "F.dds"), "rb").read()
    assert len(fix.fix_font_atlases(mod, apply_changes=False)) == 1
    assert open(os.path.join(mod, "gfx", "fonts", "F.dds"), "rb").read() == before


def test_fix_font_atlases_is_idempotent(tmp_path):
    mod = make_mod(str(tmp_path / "mod"), fonts=[cropped_font()])
    fix.fix_font_atlases(mod, apply_changes=True)
    assert fix.fix_font_atlases(mod, apply_changes=True) == []


def test_fix_font_atlases_searches_subfolders(tmp_path):
    mod = make_mod(str(tmp_path / "mod"))
    sub = os.path.join(mod, "gfx", "fonts", "chinese")
    os.makedirs(sub)
    open(os.path.join(sub, "C.fnt"), "wb").write(make_fnt(2048, 4096, [(0, 208)]))
    open(os.path.join(sub, "C.dds"), "wb").write(make_dds(2048, 208, b"DXT5"))
    assert fix.fix_font_atlases(mod, False) == \
        ["  fix   gfx/fonts/chinese/C.dds (2048x208 -> 2048x4096)"]


def test_fix_font_atlases_refuses_when_the_crop_removed_glyph_pixels(tmp_path):
    mod = make_mod(str(tmp_path / "mod"), fonts=[
        ("F", make_fnt(2048, 4096, [(0, 900)]), make_dds(2048, 208, b"DXT5"))])
    report = fix.fix_font_atlases(mod, apply_changes=True)
    assert "removed real pixels" in report[0]
    assert fix.parse_dds_header(
        open(os.path.join(mod, "gfx", "fonts", "F.dds"), "rb").read())["height"] == 208


def test_fix_font_atlases_reports_an_unreadable_dds(tmp_path):
    mod = make_mod(str(tmp_path / "mod"), fonts=[
        ("F", make_fnt(2048, 4096), b"not a dds file at all")])
    assert "skip" in fix.fix_font_atlases(mod, False)[0]


def test_fix_font_atlases_reports_a_texture_that_cannot_grow(tmp_path):
    mod = make_mod(str(tmp_path / "mod"), fonts=[
        ("F", make_fnt(2048, 4096, [(0, 8)]), make_dds(1024, 16, b"DXT5"))])
    assert "width is 1024" in fix.fix_font_atlases(mod, False)[0]


def test_fix_font_atlases_ignores_a_matching_texture(tmp_path):
    mod = make_mod(str(tmp_path / "mod"), fonts=[
        ("F", make_fnt(64, 64), make_dds(64, 64, b"DXT5"))])
    assert fix.fix_font_atlases(mod, False) == []


def test_fix_font_atlases_ignores_a_fnt_without_a_dds(tmp_path):
    mod = make_mod(str(tmp_path / "mod"), fonts=[("F", make_fnt(64, 64), None)])
    assert fix.fix_font_atlases(mod, False) == []


def test_fix_font_atlases_ignores_a_fnt_without_a_scale(tmp_path):
    mod = make_mod(str(tmp_path / "mod"), fonts=[
        ("F", b"info face=\"T\"\r\n", make_dds(64, 64, b"DXT5"))])
    assert fix.fix_font_atlases(mod, False) == []


def test_fix_font_atlases_returns_nothing_without_a_fonts_folder(tmp_path):
    (tmp_path / "mod").mkdir()
    assert fix.fix_font_atlases(str(tmp_path / "mod"), False) == []


# --- process and restore ------------------------------------------------------

def test_process_runs_both_repairs(tmp_path):
    mod = make_mod(str(tmp_path / "mod"), shader=SHADER, fonts=[cropped_font()])
    assert len(fix.process(mod, apply_changes=True)) == 2


def test_restore_puts_every_original_back(tmp_path):
    mod = make_mod(str(tmp_path / "mod"), shader=SHADER, fonts=[cropped_font()])
    shader_path = os.path.join(mod, "gfx", "FX", "text.shader")
    dds_path = os.path.join(mod, "gfx", "fonts", "F.dds")
    before = (open(shader_path, "rb").read(), open(dds_path, "rb").read())

    fix.process(mod, apply_changes=True)
    report = fix.restore(mod)

    assert len(report) == 2
    assert (open(shader_path, "rb").read(), open(dds_path, "rb").read()) == before
    assert not os.path.exists(os.path.join(mod, fix.BACKUP_DIR_NAME))


def test_restore_without_a_backup_says_so(tmp_path):
    (tmp_path / "mod").mkdir()
    assert fix.restore(str(tmp_path / "mod")) == \
        ["  nothing to restore: no %s folder" % fix.BACKUP_DIR_NAME]


# --- main ---------------------------------------------------------------------

def test_main_reports_without_writing(tmp_path, capsys):
    mod = make_mod(str(tmp_path / "mod"), shader=SHADER)
    assert fix.main(["--path", mod]) == 0
    out = capsys.readouterr().out
    assert "report only" in out and "1 change" in out
    assert open(os.path.join(mod, "gfx", "FX", "text.shader"), "rb").read() == SHADER.encode("utf-8")


def test_main_applies_the_changes(tmp_path, capsys):
    mod = make_mod(str(tmp_path / "mod"), shader=SHADER, fonts=[cropped_font()])
    assert fix.main(["--path", mod, "--apply"]) == 0
    assert "Done." in capsys.readouterr().out
    assert b"all(" not in open(
        os.path.join(mod, "gfx", "FX", "text.shader"), "rb").read()


def test_main_pluralises_a_single_change(tmp_path, capsys):
    mod = make_mod(str(tmp_path / "mod"), shader=SHADER + SHADER,
                   fonts=[cropped_font()])
    fix.main(["--path", mod])
    assert "write 2 changes" in capsys.readouterr().out


def test_main_restores(tmp_path, capsys):
    mod = make_mod(str(tmp_path / "mod"), shader=SHADER)
    fix.main(["--path", mod, "--apply"])
    assert fix.main(["--path", mod, "--restore"]) == 0
    assert "Restored." in capsys.readouterr().out
    assert open(os.path.join(mod, "gfx", "FX", "text.shader"), "rb").read() == SHADER.encode("utf-8")


def test_main_says_when_there_is_nothing_to_correct(tmp_path, capsys):
    mod = make_mod(str(tmp_path / "mod"))
    fix.main(["--path", mod])
    assert "nothing to correct" in capsys.readouterr().out


def test_main_reports_a_path_that_is_not_a_folder(tmp_path, capsys):
    fix.main(["--path", str(tmp_path / "absent")])
    assert "not a folder" in capsys.readouterr().out


def test_main_accepts_more_than_one_path(tmp_path, capsys):
    one = make_mod(str(tmp_path / "one"), shader=SHADER)
    two = make_mod(str(tmp_path / "two"), shader=SHADER)
    fix.main(["--path", one, "--path", two])
    assert "write 2 changes" in capsys.readouterr().out


def test_main_searches_the_default_roots(tmp_path, capsys, monkeypatch):
    root = tmp_path / "content"
    root.mkdir()
    make_mod(str(root / "394360"), shader=SHADER)
    monkeypatch.setattr(fix, "default_search_roots", lambda: [str(root)])
    assert fix.main([]) == 0
    out = capsys.readouterr().out
    assert "Found 1 mod folder." in out and "report only" in out


def test_main_returns_one_when_it_finds_no_mod(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(fix, "default_search_roots", lambda: [str(tmp_path / "x")])
    assert fix.main([]) == 1
    assert "No mod found." in capsys.readouterr().out


def test_main_counts_more_than_one_found_mod(tmp_path, capsys, monkeypatch):
    root = tmp_path / "content"
    root.mkdir()
    make_mod(str(root / "a"), shader=SHADER)
    make_mod(str(root / "b"), shader=SHADER)
    monkeypatch.setattr(fix, "default_search_roots", lambda: [str(root)])
    fix.main([])
    assert "Found 2 mod folders." in capsys.readouterr().out


def test_build_parser_exposes_the_version():
    with pytest.raises(SystemExit):
        fix.build_parser().parse_args(["--version"])


def test_patch_shader_leaves_two_swizzles_of_different_length_alone():
    source = "if(all(a.rgba == b.xy))return;"
    assert fix.patch_shader_source(source) == (source, 0)


def test_backup_keeps_the_copy_it_made_first(tmp_path):
    mod = make_mod(str(tmp_path / "mod"), shader=SHADER)
    path = os.path.join(mod, "gfx", "FX", "text.shader")
    fix._backup(mod, path, apply_changes=True)
    backup = os.path.join(mod, fix.BACKUP_DIR_NAME, "gfx", "FX", "text.shader")
    os.utime(backup, (0, 0))

    with open(path, "wb") as handle:
        handle.write(b"changed")
    fix._backup(mod, path, apply_changes=True)   # the copy is already there

    assert open(backup, "rb").read() == SHADER.encode("utf-8")
