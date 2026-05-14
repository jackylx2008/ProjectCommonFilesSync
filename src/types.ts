import * as path from 'path';

export interface AppConfig {
  scanRoot: string;
  targetFiles: string[];
  excludeDirs: string[];
}

export interface FileVersion {
  projectDir: string;
  fileName: string;
  filePath: string;
  digest: string;
  size: number;
  lineCount: number;
  modifiedMs: number;
}

export interface ProjectFileState {
  projectDir: string;
  fileName: string;
  version?: FileVersion;
}

export interface VersionGroup {
  fileName: string;
  digest: string;
  versions: FileVersion[];
}

export interface ScanResult {
  workspaceRoot: string;
  scanRoot: string;
  targetFiles: string[];
  projectDirs: string[];
  versionsByFile: Map<string, FileVersion[]>;
  groupsByFile: Map<string, VersionGroup[]>;
  statesByFile: Map<string, ProjectFileState[]>;
}

export function projectLabel(projectDir: string, scanRoot: string): string {
  const relative = path.relative(scanRoot, projectDir);
  return relative && !relative.startsWith('..') ? relative : projectDir;
}
