# 《钢铁雄心4》macOS 文字显示修复

[English](README.md) · 简体中文

一个修复脚本。它解决《钢铁雄心4》模组在 macOS 上文字位置错乱、字号被放大的问题。
同样的模组在 Windows 上显示正常。

已在 潜龙腾渊（创意工坊 ID 3504418504）、HOI4 1.17、macOS 15 上测试通过。

## 它修复什么

### 1. 文字着色器在 macOS 上无法编译

很多模组会这样写：

```hlsl
if (all(v.vColor == float4(255.f/255.f, 60.f/255.f, 0.f/255.f, 1.f))) ...
```

Windows 使用 Direct3D 和 HLSL。在 HLSL 里，两个向量比较的结果是**一个布尔向量**，
所以 `all()` 可以接受它。macOS 使用 OpenGL 和 GLSL。在 GLSL 里，同样的比较只得到
**一个布尔值**，`all()` 没有对应的重载，于是着色器链接失败：

```
Failed pixel shader:
ERROR: 0:177: No matching function for call to all(bool)
[pdxshaderparser.cpp:1295]: Failed adding pixel shader gfx/FX/text.shader(Text)
[pdxshaderparser.cpp:1303]: Failed linking shader gfx/FX/text.shader(Text)
```

`Text` 和 `Text3D` 两个效果都链接失败。游戏改用备用着色器绘制所有文字，
这就是文字偏移和放大的原因。

脚本把每次调用改写成逐分量比较。两种写法含义相同，并且在两个平台上都能编译：

```hlsl
if ((v.vColor.r == 255.f/255.f && v.vColor.g == 60.f/255.f &&
     v.vColor.b == 0.f/255.f  && v.vColor.a == 1.f)) ...
```

### 2. 字体图集比 `.fnt` 文件声明的尺寸小

某个图像工具裁掉了图集底部的空白区域，但是没有同步修改 `.fnt` 文件里的 `scaleH`。
Windows 读取贴图的真实尺寸，所以文字正常。macOS 读取声明的尺寸，
于是它只取到图集顶部一小条，再把它拉伸到整个字形上。

脚本在贴图底部补上空白行，直到真实尺寸等于声明尺寸，并且删除 mipmap 链。
每一个字形都保持原来的位置。

如果裁剪削掉了真实的字形像素，脚本会拒绝处理该图集。所以它不会损坏字体。

## 使用方法

你需要 Python 3。第一次输入 `python3` 时，macOS 会提示你安装它。

```sh
# 1. 查看它将要修改什么。这一步不写入任何文件。
python3 hoi4_mac_font_fix.py

# 2. 写入修复。
python3 hoi4_mac_font_fix.py --apply

# 3. 撤销全部修改。
python3 hoi4_mac_font_fix.py --restore
```

如果不写 `--path`，脚本会自动搜索两个常见的模组目录：

- `~/Library/Application Support/Steam/steamapps/workshop/content/394360`
- `~/Documents/Paradox Interactive/Hearts of Iron IV/mod`

只修复一个模组：

```sh
python3 hoi4_mac_font_fix.py --apply --path "/模组/所在/路径"
```

## 注意事项

- 脚本可以重复运行。第二次运行不会改动任何东西。
- 它先把每一个要修改的文件复制到模组内的 `.mac_fix_backup` 目录。
  `--restore` 会把它们放回原处，并删除该目录。
- **创意工坊模组在下次更新时会被 Steam 覆盖，修复也会一起消失。**
  更新之后请再运行一次脚本。如果想长期保留修复，请把模组文件夹复制到
  `~/Documents/Paradox Interactive/Hearts of Iron IV/mod/`，作为本地模组加载。
- 脚本只修改模组文件，不修改游戏本体文件。

## 确认修复是否生效

启动游戏，然后查看着色器错误：

```sh
grep "text.shader" ~/Documents/Paradox\ Interactive/Hearts\ of\ Iron\ IV/logs/error.log
```

没有任何输出，就说明着色器已经可以正常编译。

## 给模组作者

请把同样的修改合并到模组里。它在 Windows 上没有任何代价，
却能让模组在 macOS 和 Linux 上正常运行。规则很简单：
**不要在 P 社着色器里对向量比较调用 `all()` 或 `any()`。** 请逐分量比较。

## 测试

```sh
python3 -m pytest -q                       # 75 个测试
python3 -m coverage run --branch -m pytest -q
python3 -m coverage report -m --include="hoi4_mac_font_fix.py"
```

当前结果：75 个测试通过，语句覆盖率和分支覆盖率均为 100%。
