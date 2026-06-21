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
scan_root: "${CLOUDSTATION_ROOT}/Python/Project"
log_level: INFO
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
- `scan_root` 支持 `${CLOUDSTATION_ROOT}`，运行时会解析为当前平台的 CloudStation 根目录。
- `log_level`：桌面版日志级别，例如 `DEBUG`、`INFO`、`WARNING`。
- `target_files`：要同步和对比的公共文件名。
- `exclude_dirs`：递归扫描时跳过的目录名。

桌面版启动后会在仓库根目录的 `log/` 下写入按入口脚本命名的滚动日志文件，例如 `log/main.log`；单文件上限 10 MB，保留 5 份备份。

## CloudStation 本地配置

仓库只保留 `common.env.example`。本机运行时可复制为 `common.env` 并按平台填写 CloudStation 根目录：

```dotenv
CLOUDSTATION_ROOT_WINDOWS=C:\path\to\CloudStation
CLOUDSTATION_ROOT_MACOS=~/CloudStation
CLOUDSTATION_ROOT_LINUX=~/CloudStation
```

如果同时设置了 `CLOUDSTATION_ROOT`，它会优先于平台变量。未设置环境变量时，程序会尝试从当前项目路径中名为 `CloudStation` 的上级目录推断根目录。

## 桌面版功能

- 窗口启动后立即显示，首次扫描在后台线程执行，避免扫描目录较大时阻塞界面。
- 底部状态栏实时显示已检查目录数和已发现目标文件数；扫描完成后显示项目与目标文件统计。
- 递归扫描 `scan_root` 下的目标文件。
- 使用 `os.scandir` 降低目录遍历开销，并根据 CPU 线程数并行读取和计算文件 SHA-256（最多 8 个工作线程）。
- 大文件使用 4 MiB 分块读取，避免一次性载入内存；DLL 等二进制文件不执行无意义的文本行数统计。
- 扫描期间会阻止重复扫描，退出程序时可安全取消仍在执行的扫描任务。
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

### 扫描性能

扫描速度主要取决于目录条目数量、目标文件总大小和存储设备吞吐量。当前实现会先快速遍历目录，再并行读取匹配文件并计算 SHA-256；高核心数 CPU 和高速 SSD 可以明显缩短扫描时间。GPU 不参与扫描，因为目录遍历和文件读取属于文件系统 I/O，使用 GPU 通常无法获得有效收益。

二进制 DLL 仍会完整计算 SHA-256、文件大小和修改时间，但“行数”固定显示为 `0`。

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
- 展开版本组查看使用该版本的项目；项目状态只列出已找到对应文件的项目。
- 版本组说明显示项目数、大小、行数和修改时间，不在列表中显示 hash。
- 在项目节点右键选择 `Compare With...`，使用 VS Code 原生 diff 对比另一个项目的同名文件。
- 在版本组节点右键选择 `Compare Version With...`，可直接选择另一个版本组代表文件进行 diff。
- 如果两个文件 SHA256 一致，插件会提示内容一致，不打开 diff。
- 在版本组节点右键选择 `Copy This Version To Selected Projects`，可选择目标项目覆盖。
- 在版本组节点右键选择 `Copy This Version To Other Versions`，可按 Python 桌面版语义覆盖所有已存在但内容不同的项目。
- 在版本组节点右键选择 `Copy This Version To Missing Projects` 或 `Copy This Version To Different Projects`，可分别覆盖缺失项目或内容不同的项目。

## 常用命令

```powershell
# 桌面版语法检查
python -m py_compile main.py common_sync\scanner.py common_sync\config.py

# VS Code 插件编译
npm run compile
```
