# SSPAI_Figma to Badge

把 Figma 徽章转换成 GLB / USDZ 的 **Codex Skill + 无界面生成程序**。Blender 在后台建模和导出，不需要打开 Blender 界面。支持浅香槟金、银色材质、SVG 浮雕、贴图和可配置圆角。

**支持边界：**这不是任意图片一键生成任意 3D 模型。Skill 读取设计并建立映射，程序执行已定义的配方。公开版提供原创圆形示例；新轮廓、孔洞、独立金属块必须建立和验证自己的模板。三张 Figma 画板是输入约定，不代表任意异形已获生产验证。尖角、孔洞和多部件组合仍属实验性；详见 [支持范围](docs/support.md)。

## 先跑通：无需 Figma 账号

依赖：Python **3.10+**、Git、安装在本机的 **Blender 5.1.x**（实测版本见 [验证记录](docs/verification.md)）。Blender 自带 `bpy`、USD 和 SVG 导入器，不要向系统 Python 安装 `bpy`。Pillow 由安装脚本安装，用于验证输入图片。首次安装需要网络，之后素材与模板齐全即可离线导出。

```sh
git clone https://github.com/mmmmmmarcus/SSPAI_Figma-to-Badge.git
cd SSPAI_Figma-to-Badge
python3 scripts/setup.py
.venv/bin/python -m badgegen batch examples/demo-gold.json examples/demo-silver.json --output output
```

Windows：使用 `python` 启动安装脚本，后续把 `.venv/bin/python` 换成 `.venv\Scripts\python.exe`。Windows/Linux 尚未完成实机导出验证。Blender 不在 PATH 时，传入 `python3 scripts/setup.py --blender /absolute/path/to/blender`，并在后续命令中设置 `BLENDER_BIN` 或使用 `badgegen --blender /absolute/path/to/blender ...`。

输出：`output/demo-gold/Demo Gold.glb`、`Demo Gold.usdz` 和 `report.json`，银色在 `output/demo-silver/`。示例模板、SVG、纹理均为本项目原创；安装时生成 `.blend` 与材质纹理，**不会生成 3D PNG 预览**。离线示例没有 Figma 画板，因此不伪造彩色/灰色交付 PNG。

## 安装 Skill

```sh
.venv/bin/python scripts/install_skill.py
```

重新加载 Codex 的技能列表或开启新会话，给出指令：

> 使用 $figma-badge-3d，根据这个 Figma 徽章链接生成 GLB、USDZ，以及原画板的彩色和灰色 PNG。

需要你的 AI 环境已连接 Figma MCP，且能读取该文件、导出 SVG/PNG。推荐本地 Figma Desktop MCP；登录、授权由你自己的 Figma 账号管理，仓库不包含账号、API key 或专用设计文件。离线示例不能代替 Figma 集成配置。若 MCP 无法原生导出完整画板，使用 Figma 的 Export；不要拿画布截图当素材。

安装器拒绝覆盖已有 Skill；`--replace` 显式更新并保留已有 `config.json`。`BADGEGEN_ROOT` 和 helper 的 `--generator` 可以指定其他兼容生成器。**移动/删除源码目录会使安装后的 Skill 失效**，需重新指定路径。始终用该 checkout 的 `.venv` Python 执行 helper，以确保 Pillow 可用。

## 工作流与交付

1. Figma 同一父容器下放置 `徽章名`、`徽章名_灰`、`Blender贴图` 三个画板；默认 helper 要求同列、贴图为方形。矢量需闭合、描边轮廓化、文字转曲、遮罩展平。
2. AI 查看当前分层和视觉参考，确定几何/贴图/辅助层，匹配或新建模板。文件名不用于猜测结构。
3. 取得当前 SVG、专用贴图和两个原生 PNG，记录来源，生成配方。
4. 后台导出并重新导入 **实际 GLB 和 USDZ**，检查容器、尺寸、对象、材质贴图及启用的几何检查。
5. 交付与图层同名的 GLB/USDZ、彩色/灰色 PNG；PNG 原样复制，展示端提供镜头、灯光和环境反射。

专用 `Blender贴图` 是第三层，确实参与模型材质。完整徽章 PNG 只是交付图，不可当贴图，否则会重复 2D 高光/浮雕。

- [Figma 映射与 plan 格式](skills/figma-badge-3d/references/figma-mapping.md)
- [模板、配方与材质](docs/recipes.md)
- [支持范围与限制](docs/support.md)
- [安装排错](docs/troubleshooting.md)
- [验证记录](docs/verification.md)
- [贡献方式](CONTRIBUTING.md)

## CLI

```sh
.venv/bin/python -m badgegen doctor
.venv/bin/python -m badgegen inspect examples/demo-gold.json
.venv/bin/python -m badgegen build examples/demo-silver.json --output output
.venv/bin/python -m badgegen validate 'output/demo-silver/Demo Silver.glb' 'output/demo-silver/Demo Silver.usdz'
.venv/bin/python -m unittest discover -s tests -v
```

CLI 输出 JSON、错误返回非零状态；Blender 日志在输出目录的 `build.log`。构建按素材、代码和 Blender 指纹缓存，仅在新输出校验通过后替换旧结果。批量构建会保留成功项并报告失败项。可用 `--force` 强制重建。可选 `compare` 是开发检查工具，Skill 不调用它、不生成预览图。

## English quick overview

A Codex Skill orchestrates Figma extraction and explicit geometry mapping; a Python CLI runs Blender headlessly and exports self-contained GLB/USDZ. Start with `python3 scripts/setup.py`, then run the demo batch above. Install the Skill with `scripts/install_skill.py`. Figma access requires your own MCP connection. New silhouettes require new validated templates; arbitrary-shape production support is **not** implied. The repository contains only original synthetic samples, never private customer artwork.

MIT license for this repository's code and original samples. Blender and Python dependencies retain their own licenses and are installed separately. See [LICENSE](LICENSE).
