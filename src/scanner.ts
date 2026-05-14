import * as crypto from 'crypto';
import * as fs from 'fs/promises';
import * as path from 'path';
import { AppConfig, FileVersion, ProjectFileState, ScanResult, VersionGroup } from './types';

export async function scanProjects(config: AppConfig, workspaceRoot: string): Promise<ScanResult> {
  const targetNames = new Set(config.targetFiles);
  const excludeDirs = new Set(config.excludeDirs);
  const versionsByFile = new Map<string, FileVersion[]>();
  const projectDirs = new Set<string>();
  for (const fileName of config.targetFiles) {
    versionsByFile.set(fileName, []);
  }

  for await (const filePath of iterTargetFiles(config.scanRoot, targetNames, excludeDirs)) {
    const projectDir = path.dirname(filePath);
    if (isRelativeTo(projectDir, workspaceRoot)) {
      continue;
    }
    const version = await readVersion(filePath);
    versionsByFile.get(version.fileName)?.push(version);
    projectDirs.add(projectDir);
  }

  const sortedProjects = Array.from(projectDirs).sort(comparePath);
  for (const versions of versionsByFile.values()) {
    versions.sort((left, right) => comparePath(left.projectDir, right.projectDir));
  }

  const groupsByFile = new Map<string, VersionGroup[]>();
  const statesByFile = new Map<string, ProjectFileState[]>();
  for (const fileName of config.targetFiles) {
    const versions = versionsByFile.get(fileName) ?? [];
    groupsByFile.set(fileName, groupVersions(fileName, versions));
    statesByFile.set(
      fileName,
      sortedProjects.map((projectDir) => ({
        projectDir,
        fileName,
        version: versions.find((item) => item.projectDir === projectDir)
      }))
    );
  }

  return {
    workspaceRoot,
    scanRoot: config.scanRoot,
    targetFiles: config.targetFiles,
    projectDirs: sortedProjects,
    versionsByFile,
    groupsByFile,
    statesByFile
  };
}

export async function copyVersionToProjects(source: FileVersion, targetProjects: string[]): Promise<string[]> {
  const copied: string[] = [];
  for (const projectDir of targetProjects) {
    const targetPath = path.join(projectDir, source.fileName);
    if (path.resolve(targetPath) === path.resolve(source.filePath)) {
      continue;
    }
    await fs.mkdir(path.dirname(targetPath), { recursive: true });
    await fs.copyFile(source.filePath, targetPath);
    copied.push(targetPath);
  }
  return copied;
}

async function* iterTargetFiles(
  scanRoot: string,
  targetNames: Set<string>,
  excludeDirs: Set<string>
): AsyncGenerator<string> {
  const stack = [path.resolve(scanRoot)];
  while (stack.length > 0) {
    const directory = stack.pop();
    if (!directory) {
      continue;
    }
    let entries: import('fs').Dirent[];
    try {
      entries = await fs.readdir(directory, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      const entryPath = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        if (!excludeDirs.has(entry.name)) {
          stack.push(entryPath);
        }
      } else if (entry.isFile() && targetNames.has(entry.name)) {
        yield entryPath;
      }
    }
  }
}

async function readVersion(filePath: string): Promise<FileVersion> {
  const data = await fs.readFile(filePath);
  const stat = await fs.stat(filePath);
  return {
    projectDir: path.dirname(filePath),
    fileName: path.basename(filePath),
    filePath,
    digest: crypto.createHash('sha256').update(data).digest('hex'),
    size: stat.size,
    lineCount: data.toString('utf8').split(/\r\n|\r|\n/).length,
    modifiedMs: stat.mtimeMs
  };
}

function groupVersions(fileName: string, versions: FileVersion[]): VersionGroup[] {
  const buckets = new Map<string, FileVersion[]>();
  for (const version of versions) {
    const bucket = buckets.get(version.digest) ?? [];
    bucket.push(version);
    buckets.set(version.digest, bucket);
  }
  return Array.from(buckets.entries())
    .map(([digest, items]) => ({ fileName, digest, versions: items }))
    .sort((left, right) => right.versions.length - left.versions.length || left.digest.localeCompare(right.digest));
}

function comparePath(left: string, right: string): number {
  return left.toLocaleLowerCase().localeCompare(right.toLocaleLowerCase());
}

function isRelativeTo(child: string, parent: string): boolean {
  const relative = path.relative(path.resolve(parent), path.resolve(child));
  return relative === '' || Boolean(relative && !relative.startsWith('..') && !path.isAbsolute(relative));
}
