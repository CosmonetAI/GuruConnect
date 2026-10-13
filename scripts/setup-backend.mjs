import { spawnSync } from 'node:child_process';
import { existsSync, readdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
const project = fileURLToPath(new URL('../', import.meta.url));
const versions = join(process.env.PYENV_ROOT || join(homedir(), '.pyenv'), 'versions');
const managed = existsSync(versions) ? readdirSync(versions).filter(version => /^3\.(11|12)\./.test(version)).sort().reverse().map(version => join(versions, version, 'bin', 'python')) : [];
const candidates = [process.env.GURUCONNECT_PYTHON, 'python3.12', 'python3.11', ...managed, 'python3'].filter(Boolean);
const python = candidates.find(command => spawnSync(command, ['-c', 'import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] < (3,13) else 1)'], { stdio: 'ignore' }).status === 0);
if (!python) { console.error('Install Python 3.11 or 3.12, or set GURUCONNECT_PYTHON to its executable path, then run npm run setup again.'); process.exit(1); }
const backend = join(project, 'services', 'backend');
const environment = join(backend, '.venv');
if (!existsSync(environment)) {
  const created = spawnSync(python, ['-m', 'venv', environment], { stdio: 'inherit' });
  if (created.status !== 0) process.exit(created.status ?? 1);
}
const executable = process.platform === 'win32' ? join(environment, 'Scripts', 'python.exe') : join(environment, 'bin', 'python');
const result = spawnSync(executable, ['-m', 'pip', 'install', '-r', 'requirements-dev.txt'], { cwd: backend, stdio: 'inherit' });
if (result.status !== 0) process.exit(result.status ?? 1);
console.log('GuruConnect backend is installed. Run npm run dev to start the frontend, API and worker.');
