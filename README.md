# Project Common Files Sync

用于扫描多个同级项目中的公共文件，按内容聚合版本，查看差异，并把选定版本同步到其它项目。

这个仓库同时包含两个入口：

- `main.py`：PySide6 桌面工具。
- `src/`：VS Code 插件源码，当前分支为 `vscode-plugin`。

## 桌面版运行

```powershell
pip install -r requirements.txt
python main.py
```

## 配置

编辑仓库根目录下的 `config.yaml`：

```yaml
scan_root: ..
target_files:
  - .flake8
  - LOCAL_AI_RUNTIME_SETUP.md
  - COMMON_PROJECT_SKILLS.md
exclude_dirs:
  - .git
  - .venv
  - venv
  - env
  - node_modules
  - __pycache__
  - .pytest_cache
  - .mypy_cache
  - .ruff_cache
  - dist
  - build
```

字段说明：

- `scan_root`：要扫描的根目录，通常是当前项目的上一级目录。
- `target_files`：要同步和对比的公共文件名。
- `exclude_dirs`：递归扫描时跳过的目录名。

## 桌面版功能

- 递归扫描 `scan_root` 下的目标文件。
- “目标文件”列表展示每个目标文件的存在项目数、版本组数和缺失数。
- “内容相同的版本组”按文件内容聚合版本，显示项目数、大小、行数、修改时间和代表项目。
- “项目状态”只显示已找到对应文件的项目，避免缺失项干扰对比。
- 可选择版本组作为源版本，并覆盖勾选的项目。
- 可选择一个版本组，直接“覆盖其他版本”，把该版本写入所有其它内容不同的项目。
- 可在项目状态或版本组中选择两个版本，查看左右并排差异。
- 差异视图支持左右滚动同步，使用类似 VS Code Git diff 的红/绿底色标记不同内容。
- 差异视图中间提供左右箭头按钮，可选择左侧覆盖右侧或右侧覆盖左侧。
- 差异视图下方显示两侧版本信息，包括项目、版本摘要、大小、行数、修改时间和文件路径。

覆盖操作都会弹出确认框，并显示源文件和目标文件或目标项目预览。

## VS Code 插件开发

安装依赖并编译：

```powershell
npm install
npm run compile
```

在 VS Code 中按 `F5`，选择 `Run VS Code Extension` 启动调试宿主。

插件会复用 `config.yaml`，在活动栏 `Common Files` 视图中展示目标文件、内容版本组和项目状态。

## VS Code 插件功能

- 点击 `Common Files` 视图标题栏刷新按钮重新扫描。
- 展开目标文件查看内容相同的版本组。
- 展开版本组查看使用该版本的项目。
- 在项目节点右键选择 `Compare With...`，使用 VS Code 原生 diff 对比另一个项目的同名文件。
- 如果两个文件 SHA256 一致，插件会提示内容一致，不打开 diff。
- 在版本组节点右键选择 `Copy This Version To Selected Projects`，可选择目标项目覆盖。
- 在版本组节点右键选择 `Copy This Version To Missing Projects` 或 `Copy This Version To Different Projects`，可批量覆盖缺失或内容不同的项目。

## 常用命令

```powershell
# 桌面版语法检查
python -m py_compile main.py common_sync\scanner.py common_sync\config.py

# VS Code 插件编译
npm run compile
```
