import * as path from 'path';
import * as vscode from 'vscode';
import { loadConfig } from './config';
import { scanProjects } from './scanner';
import { FileVersion, ProjectFileState, projectLabel, ScanResult, VersionGroup } from './types';

export type TreeNode = FileNode | GroupNode | MissingNode | ProjectNode;

export class CommonFilesTreeProvider implements vscode.TreeDataProvider<TreeNode> {
  private readonly changeEmitter = new vscode.EventEmitter<TreeNode | undefined | null | void>();
  readonly onDidChangeTreeData = this.changeEmitter.event;

  private result?: ScanResult;
  private loading = false;

  constructor(private readonly workspaceRoot: string) {}

  async refresh(): Promise<void> {
    this.loading = true;
    this.changeEmitter.fire();
    try {
      const config = await loadConfig(this.workspaceRoot);
      this.result = await scanProjects(config, this.workspaceRoot);
    } finally {
      this.loading = false;
      this.changeEmitter.fire();
    }
  }

  getResult(): ScanResult | undefined {
    return this.result;
  }

  getTreeItem(element: TreeNode): vscode.TreeItem {
    return element;
  }

  async getChildren(element?: TreeNode): Promise<TreeNode[]> {
    if (this.loading) {
      return [];
    }
    if (!this.result) {
      await this.refresh();
    }
    if (!this.result) {
      return [];
    }
    if (!element) {
      return this.result.targetFiles.map((fileName) => new FileNode(this.result!, fileName));
    }
    if (element instanceof FileNode) {
      const groups = this.result.groupsByFile.get(element.fileName) ?? [];
      const missingCount = this.missingStates(element.fileName).length;
      const nodes: TreeNode[] = groups.map((group, index) => new GroupNode(this.result!, group, index));
      if (missingCount > 0) {
        nodes.push(new MissingNode(this.result, element.fileName, missingCount));
      }
      return nodes;
    }
    if (element instanceof GroupNode) {
      return element.group.versions.map((version) => (
        new ProjectNode(this.result!, {
          projectDir: version.projectDir,
          fileName: version.fileName,
          version
        })
      ));
    }
    if (element instanceof MissingNode) {
      return this.missingStates(element.fileName).map((state) => new ProjectNode(this.result!, state));
    }
    return [];
  }

  private missingStates(fileName: string): ProjectFileState[] {
    return this.result?.statesByFile.get(fileName)?.filter((state) => !state.version) ?? [];
  }
}

export class FileNode extends vscode.TreeItem {
  readonly contextValue = 'targetFile';

  constructor(readonly result: ScanResult, readonly fileName: string) {
    const states = result.statesByFile.get(fileName) ?? [];
    const existing = states.filter((state) => state.version).length;
    const missing = states.length - existing;
    const groups = result.groupsByFile.get(fileName)?.length ?? 0;
    super(fileName, vscode.TreeItemCollapsibleState.Expanded);
    this.description = `${existing} projects, ${groups} versions, ${missing} missing`;
    this.tooltip = [
      `File: ${fileName}`,
      `Existing projects: ${existing}`,
      `Version groups: ${groups}`,
      `Missing projects: ${missing}`
    ].join('\n');
    this.iconPath = new vscode.ThemeIcon('file-code');
  }
}

export class GroupNode extends vscode.TreeItem {
  readonly contextValue = 'versionGroup';

  constructor(readonly result: ScanResult, readonly group: VersionGroup, index: number) {
    const rep = group.versions[0];
    super(`Version ${index + 1}`, vscode.TreeItemCollapsibleState.Collapsed);
    this.description = `${group.versions.length} projects, ${rep.size} bytes, ${rep.lineCount} lines`;
    this.tooltip = [
      `SHA256: ${group.digest}`,
      `Representative: ${rep.filePath}`,
      `Projects: ${group.versions.length}`,
      `Size: ${rep.size}`,
      `Lines: ${rep.lineCount}`
    ].join('\n');
    this.iconPath = new vscode.ThemeIcon('versions');
  }
}

export class MissingNode extends vscode.TreeItem {
  readonly contextValue = 'missingGroup';

  constructor(readonly result: ScanResult, readonly fileName: string, missingCount: number) {
    super('Missing', vscode.TreeItemCollapsibleState.Collapsed);
    this.description = `${missingCount} projects`;
    this.tooltip = `${missingCount} projects do not have ${fileName}.`;
    this.iconPath = new vscode.ThemeIcon('warning');
  }
}

export class ProjectNode extends vscode.TreeItem {
  readonly contextValue = 'projectState';

  constructor(readonly result: ScanResult, readonly state: ProjectFileState) {
    super(projectLabel(state.projectDir, result.scanRoot), vscode.TreeItemCollapsibleState.None);
    this.description = state.version ? `${state.version.digest.slice(0, 12)} ${state.version.size} bytes` : 'missing';
    this.tooltip = buildProjectTooltip(result, state);
    this.iconPath = new vscode.ThemeIcon(state.version ? 'file' : 'circle-slash');
    if (state.version) {
      this.resourceUri = vscode.Uri.file(state.version.filePath);
      this.command = {
        command: 'projectCommonFilesSync.openFile',
        title: 'Open File',
        arguments: [this]
      };
    }
  }
}

function buildProjectTooltip(result: ScanResult, state: ProjectFileState): string {
  if (!state.version) {
    return [
      `Project: ${projectLabel(state.projectDir, result.scanRoot)}`,
      `File: ${state.fileName}`,
      'Status: missing'
    ].join('\n');
  }
  return [
    `Project: ${projectLabel(state.projectDir, result.scanRoot)}`,
    `File: ${state.version.filePath}`,
    `SHA256: ${state.version.digest}`,
    `Size: ${state.version.size}`,
    `Lines: ${state.version.lineCount}`,
    `Modified: ${new Date(state.version.modifiedMs).toLocaleString()}`
  ].join('\n');
}

export function versionQuickPickItem(version: FileVersion, scanRoot: string): vscode.QuickPickItem & { version: FileVersion } {
  return {
    label: projectLabel(version.projectDir, scanRoot),
    description: version.digest.slice(0, 12),
    detail: path.relative(scanRoot, version.filePath),
    version
  };
}
