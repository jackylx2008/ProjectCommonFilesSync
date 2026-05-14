import * as fs from 'fs/promises';
import * as path from 'path';
import * as yaml from 'js-yaml';
import { AppConfig } from './types';

const defaultTargetFiles = ['.flake8', 'LOCAL_AI_RUNTIME_SETUP.md', 'COMMON_PROJECT_SKILLS.md'];
const defaultExcludeDirs = [
  '.git',
  '.venv',
  'venv',
  'env',
  'node_modules',
  '__pycache__',
  '.pytest_cache',
  '.mypy_cache',
  '.ruff_cache',
  'dist',
  'build'
];

interface RawConfig {
  scan_root?: unknown;
  target_files?: unknown;
  exclude_dirs?: unknown;
}

export async function loadConfig(workspaceRoot: string): Promise<AppConfig> {
  const configPath = path.join(workspaceRoot, 'config.yaml');
  let raw: RawConfig = {};
  try {
    const text = await fs.readFile(configPath, 'utf8');
    const loaded = yaml.load(text);
    if (loaded && typeof loaded === 'object') {
      raw = loaded as RawConfig;
    }
  } catch (error) {
    raw = {};
  }

  const scanRootText = typeof raw.scan_root === 'string' && raw.scan_root.trim()
    ? raw.scan_root
    : '..';
  const scanRoot = path.resolve(workspaceRoot, scanRootText);
  return {
    scanRoot,
    targetFiles: toStringList(raw.target_files, defaultTargetFiles),
    excludeDirs: toStringList(raw.exclude_dirs, defaultExcludeDirs)
  };
}

function toStringList(value: unknown, fallback: string[]): string[] {
  if (!Array.isArray(value)) {
    return fallback;
  }
  const items = value.map((item) => String(item).trim()).filter(Boolean);
  return items.length > 0 ? items : fallback;
}
