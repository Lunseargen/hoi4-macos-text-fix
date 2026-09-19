# HOI4 macOS text fix

A script that repairs Hearts of Iron IV mods whose text renders in the wrong
position and at the wrong size on macOS, but looks correct on Windows.

Tested on 潜龙腾渊 (Workshop id 3504418504), HOI4 1.17, macOS 15.

## What it corrects

### 1. A text shader that does not compile on macOS

Many mods write this:

```hlsl
if (all(v.vColor == float4(255.f/255.f, 60.f/255.f, 0.f/255.f, 1.f))) ...
```

Windows uses Direct3D and HLSL. There, comparing two vectors gives a **vector of
booleans**, so `all()` accepts it. macOS uses OpenGL and GLSL. There, the same
comparison gives **one boolean**, so `all()` has no matching overload:

```
Failed pixel shader:
ERROR: 0:177: No matching function for call to all(bool)
[pdxshaderparser.cpp:1295]: Failed adding pixel shader gfx/FX/text.shader(Text)
[pdxshaderparser.cpp:1303]: Failed linking shader gfx/FX/text.shader(Text)
```

Both the `Text` and `Text3D` effects fail to link. The game then draws every
string with a fallback program, which is why the text is shifted and magnified.

The script rewrites each call as a comparison of one component at a time. That
means the same thing and compiles on both platforms:

```hlsl
if ((v.vColor.r == 255.f/255.f && v.vColor.g == 60.f/255.f &&
     v.vColor.b == 0.f/255.f  && v.vColor.a == 1.f)) ...
```

### 2. A font atlas that is smaller than its `.fnt` file declares

An image tool cropped the empty bottom off some atlas textures, but nobody
updated `scaleH` in the matching `.fnt` file. Windows takes the real size of the
texture, so text stays correct. macOS takes the declared size, so it reads a
small strip of the atlas and stretches it over the glyph.

The script adds empty rows to the bottom until the real size equals the declared
size, and removes the mip chain. Every glyph keeps its original position.

The script refuses to touch an atlas whose crop removed real glyph pixels, so it
cannot damage a font.

## How to use it

You need Python 3. macOS offers to install it the first time you type `python3`.

```sh
# 1. See what it would change. This writes nothing.
python3 hoi4_mac_font_fix.py

# 2. Write the corrections.
python3 hoi4_mac_font_fix.py --apply

# 3. Undo everything.
python3 hoi4_mac_font_fix.py --restore
```

With no `--path`, the script searches both usual mod locations:

- `~/Library/Application Support/Steam/steamapps/workshop/content/394360`
- `~/Documents/Paradox Interactive/Hearts of Iron IV/mod`

To repair one mod only:

```sh
python3 hoi4_mac_font_fix.py --apply --path "/path/to/the/mod"
```

## Notes

- The script is safe to run more than once. The second run changes nothing.
- It copies every file it touches into a `.mac_fix_backup` folder inside the mod
  first. `--restore` puts them back and removes that folder.
- **Steam replaces a Workshop mod on its next update, which removes these
  corrections.** Run the script again after an update. To keep the corrections
  for good, copy the mod folder to
  `~/Documents/Paradox Interactive/Hearts of Iron IV/mod/` and load it as a
  local mod instead.
- The script changes no game files, only mod files.

## Checking that it worked

Start the game, then look for the shader error:

```sh
grep "text.shader" ~/Documents/Paradox\ Interactive/Hearts\ of\ Iron\ IV/logs/error.log
```

An empty result means the shader now compiles.

## For mod authors

Please apply the same change upstream. It costs nothing on Windows and it makes
the mod work on macOS and Linux. The rule is short: **never call `all()` or
`any()` on a vector comparison in a Paradox shader.** Compare the components.

## Tests

```sh
python3 -m pytest -q                       # 75 tests
python3 -m coverage run --branch -m pytest -q
python3 -m coverage report -m --include="hoi4_mac_font_fix.py"
```

Current result: 75 passed, 100% statement and branch coverage.
