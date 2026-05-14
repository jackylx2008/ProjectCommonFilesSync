# Project Common Files Sync

本工具用于扫描当前项目上一级目录下的其他项目，统计并对比公共配置文件版本，并支持选择一个版本覆盖到其他项目。

## 运行

```powershell
pip install -r requirements.txt
python main.py
```

## 配置

编辑 `config.yaml`：

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

## 功能

- 递归扫描 `scan_root` 下的目标文件。
- 按文件名统计每个项目中的版本。
- 将内容完全相同的文件自动归为同一版本组。
- 选择同名文件的两个项目后，左右并排查看差异。
- 选择一个版本组，勾选目标项目后，可将该版本覆盖到目标项目。
