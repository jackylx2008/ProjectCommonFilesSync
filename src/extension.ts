import * as path from 'path';
import * as vscode from 'vscode';
import { copyVersionToProjects } from './scanner';
import { FileVersion, ProjectFileState, projectLabel, VersionGroup } from './types';
import { CommonFilesTreeProvider, GroupNode, ProjectNode, versionQuickPickItem } from './treeProvider';

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!workspaceRoot) {
    vscode.window.showWarningMessage('Project Common Files Sync requires an open workspace.');
    return;
  }

  const provider = new CommonFilesTreeProvider(workspaceRoot);
  context.subscriptions.push(
    vscode.window.registerTreeDataProvider('projectCommonFilesSync.files', provider),
    vscode.commands.registerCommand('projectCommonFilesSync.refresh', async () => {
      await runWithProgress('Scanning project common files...', () => provider.refresh());
    }),
    vscode.commands.registerCommand('projectCommonFilesSync.openFile', async (node?: ProjectNode) => {
      const version = node?.state.version;
      if (!version) {
        return;
      }
      await vscode.window.showTextDocument(vscode.Uri.file(version.filePath));
    }),
    vscode.commands.registerCommand('projectCommonFilesSync.compareProject', async (node?: ProjectNode) => {
      await compareProject(provider, node);
    }),
    vscode.commands.registerCommand('projectCommonFilesSync.copyGroupToSelected', async (node?: GroupNode) => {
      await copyGroupToSelected(provider, node);
    }),
    vscode.commands.registerCommand('projectCommonFilesSync.copyGroupToMissing', async (node?: GroupNode) => {
      await copyGroup(provider, node, 'missing');
    }),
    vscode.commands.registerCommand('projectCommonFilesSync.copyGroupToDifferent', async (node?: GroupNode) => {
      await copyGroup(provider, node, 'different');
    })
  );

  await runWithProgress('Scanning project common files...', () => provider.refresh());
}

export function deactivate(): void {}

async function compareProject(provider: CommonFilesTreeProvider, node?: ProjectNode): Promise<void> {
  const result = provider.getResult();
  if (!result || !node) {
    vscode.window.showInformationMessage('Select a project file node first.');
    return;
  }
  if (!node.state.version) {
    vscode.window.showInformationMessage(`${projectLabel(node.state.projectDir, result.scanRoot)} does not have ${node.state.fileName}.`);
    return;
  }

  const candidates = (result.statesByFile.get(node.state.fileName) ?? [])
    .filter((state) => state.version && state.projectDir !== node.state.projectDir);
  if (candidates.length === 0) {
    vscode.window.showInformationMessage(`No other projects have ${node.state.fileName}.`);
    return;
  }

  const picked = await vscode.window.showQuickPick(
    candidates.map((state) => ({
      label: projectLabel(state.projectDir, result.scanRoot),
      description: state.version!.digest.slice(0, 12),
      detail: state.version!.filePath,
      state
    })),
    {
      title: `Compare ${node.state.fileName}`,
      placeHolder: 'Choose another project file to compare'
    }
  );
  if (!picked?.state.version) {
    return;
  }

  await openDiff(node.state.version, picked.state.version);
}

async function openDiff(left: FileVersion, right: FileVersion): Promise<void> {
  if (left.digest === right.digest) {
    vscode.window.showInformationMessage(`Content is identical. SHA256: ${left.digest}`);
    return;
  }
  await vscode.commands.executeCommand(
    'vscode.diff',
    vscode.Uri.file(left.filePath),
    vscode.Uri.file(right.filePath),
    `${path.basename(left.projectDir)} ↔ ${path.basename(right.projectDir)} (${left.fileName})`
  );
}

async function copyGroupToSelected(provider: CommonFilesTreeProvider, node: GroupNode | undefined): Promise<void> {
  const result = provider.getResult();
  if (!result || !node) {
    vscode.window.showInformationMessage('Select a version group first.');
    return;
  }

  const source = await chooseSourceVersion(node.group, result.scanRoot);
  if (!source) {
    return;
  }
  const states = (result.statesByFile.get(node.group.fileName) ?? [])
    .filter((state) => state.projectDir !== source.projectDir);
  const picked = await vscode.window.showQuickPick(
    states.map((state) => ({
      label: projectLabel(state.projectDir, result.scanRoot),
      description: state.version ? state.version.digest.slice(0, 12) : 'missing',
      detail: state.version ? state.version.filePath : path.join(state.projectDir, state.fileName),
      picked: !state.version || state.version.digest !== source.digest,
      state
    })),
    {
      title: `Copy ${node.group.fileName} from ${projectLabel(source.projectDir, result.scanRoot)}`,
      placeHolder: 'Choose target projects',
      canPickMany: true
    }
  );
  if (!picked || picked.length === 0) {
    return;
  }

  const targets = picked.map((item) => item.state);
  await confirmAndCopy(provider, node.group, source, targets, 'selected');
}

async function copyGroup(provider: CommonFilesTreeProvider, node: GroupNode | undefined, mode: 'missing' | 'different'): Promise<void> {
  const result = provider.getResult();
  if (!result || !node) {
    vscode.window.showInformationMessage('Select a version group first.');
    return;
  }

  const source = await chooseSourceVersion(node.group, result.scanRoot);
  if (!source) {
    return;
  }
  const states = result.statesByFile.get(node.group.fileName) ?? [];
  const targets = states.filter((state) => shouldCopyToState(state, source, mode));
  if (targets.length === 0) {
    vscode.window.showInformationMessage(`No ${mode} projects need ${node.group.fileName}.`);
    return;
  }

  await confirmAndCopy(provider, node.group, source, targets, mode);
}

async function confirmAndCopy(
  provider: CommonFilesTreeProvider,
  group: VersionGroup,
  source: FileVersion,
  targets: ProjectFileState[],
  mode: string
): Promise<void> {
  const result = provider.getResult();
  if (!result) {
    return;
  }
  const targetNames = targets.slice(0, 8).map((state) => `- ${projectLabel(state.projectDir, result.scanRoot)}`).join('\n');
  const extra = targets.length > 8 ? `\n- ... and ${targets.length - 8} more` : '';
  const choice = await vscode.window.showWarningMessage(
    `Copy ${source.filePath} to ${targets.length} ${mode} project(s)?\n\n${targetNames}${extra}`,
    { modal: true },
    'Copy'
  );
  if (choice !== 'Copy') {
    return;
  }

  const copied = await copyVersionToProjects(source, targets.map((state) => state.projectDir));
  await provider.refresh();
  vscode.window.showInformationMessage(`Copied ${group.fileName} to ${copied.length} project(s).`);
}

function shouldCopyToState(state: ProjectFileState, source: FileVersion, mode: 'missing' | 'different'): boolean {
  if (state.projectDir === source.projectDir) {
    return false;
  }
  if (!state.version) {
    return true;
  }
  return mode === 'different' && state.version.digest !== source.digest;
}

async function chooseSourceVersion(group: VersionGroup, scanRoot: string): Promise<FileVersion | undefined> {
  if (group.versions.length === 1) {
    return group.versions[0];
  }
  const picked = await vscode.window.showQuickPick(
    group.versions.map((version) => versionQuickPickItem(version, scanRoot)),
    {
      title: `Choose source project for ${group.fileName}`,
      placeHolder: 'Files in this version group have identical content'
    }
  );
  return picked?.version;
}

async function runWithProgress<T>(title: string, task: () => Promise<T>): Promise<T> {
  return vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Window,
      title
    },
    task
  );
}
