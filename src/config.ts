import * as fs from 'fs/promises';
import * as os from 'os';
import * as path from 'path';
import * as yaml from 'js-yaml';
import { AppConfig } from './types';

const defaultTargetFiles = ['.flake8', 'LOCAL_AI_RUNTIME_SETUP.md', 'COMMON_PROJECT_SKILLS.md'];
const envPattern = /\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}/g;
const platformCloudstationEnv: Record<NodeJS.Platform, string | undefined> = {
  win32: 'CLOUDSTATION_ROOT_WINDOWS',
  darwin: 'CLOUDSTATION_ROOT_MACOS',
  linux: 'CLOUDSTATION_ROOT_LINUX',
  aix: undefined,
  android: undefined,
  freebsd: undefined,
  haiku: undefined,
  openbsd: undefined,
  sunos: undefined,
  cygwin: undefined,
  netbsd: undefined
};
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
  await loadCommonEnv(path.join(workspaceRoot, 'common.env'));
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
  const scanRoot = resolveConfigPath(scanRootText, workspaceRoot);
  return {
    scanRoot,
    targetFiles: toStringList(raw.target_files, defaultTargetFiles),
    excludeDirs: toStringList(raw.exclude_dirs, defaultExcludeDirs)
  };
}

async function loadCommonEnv(envPath: string): Promise<void> {
  let text: string;
  try {
    text = await fs.readFile(envPath, 'utf8');
  } catch {
    return;
  }
  for (const line of text.split(/\r\n|\r|\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#') || !trimmed.includes('=')) {
      continue;
    }
    const [rawKey, ...rawValueParts] = trimmed.split('=');
    const key = rawKey.trim();
    const value = rawValueParts.join('=').trim().replace(/^['"]|['"]$/g, '');
    if (key && process.env[key] === undefined) {
      process.env[key] = value;
    }
  }
}

function resolveConfigPath(rawPath: string, workspaceRoot: string): string {
  const resolved = resolveEnvMarkers(rawPath, workspaceRoot);
  const expanded = resolved.startsWith('~')
    ? path.join(os.homedir(), resolved.slice(1))
    : resolved;
  return path.resolve(workspaceRoot, expanded);
}

function resolveEnvMarkers(value: string, workspaceRoot: string): string {
  const cloudstationRoot = selectCloudstationRoot(workspaceRoot);
  return value.replace(envPattern, (_match, name: string, fallback: string | undefined) => {
    if (name === 'CLOUDSTATION_ROOT' && cloudstationRoot) {
      return cloudstationRoot;
    }
    const envValue = process.env[name];
    if (envValue !== undefined) {
      return envValue;
    }
    if (fallback !== undefined) {
      return fallback;
    }
    throw new Error(`Missing environment variable for config path: ${name}`);
  });
}

function selectCloudstationRoot(workspaceRoot: string): string | undefined {
  const explicit = process.env.CLOUDSTATION_ROOT;
  if (explicit) {
    return expandUser(explicit);
  }
  const platformVar = platformCloudstationEnv[process.platform];
  if (platformVar && process.env[platformVar]) {
    return expandUser(process.env[platformVar]!);
  }
  return inferCloudstationRoot(workspaceRoot);
}

function inferCloudstationRoot(workspaceRoot: string): string | undefined {
  let current = path.resolve(workspaceRoot);
  while (true) {
    if (path.basename(current).toLocaleLowerCase() === 'cloudstation') {
      return current;
    }
    const parent = path.dirname(current);
    if (parent === current) {
      return undefined;
    }
    current = parent;
  }
}

function expandUser(value: string): string {
  return value.startsWith('~') ? path.join(os.homedir(), value.slice(1)) : value;
}

function toStringList(value: unknown, fallback: string[]): string[] {
  if (!Array.isArray(value)) {
    return fallback;
  }
  const items = value.map((item) => String(item).trim()).filter(Boolean);
  return items.length > 0 ? items : fallback;
}
