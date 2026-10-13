import { spawn } from 'node:child_process';
import { accessSync, constants } from 'node:fs';
import { constants as osConstants } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const projectDirectory = fileURLToPath(new URL('../', import.meta.url));

export function createLaunchPlan(mode, extraArguments = [], project = projectDirectory) {
  if (!['api', 'worker'].includes(mode)) {
    throw new Error('Choose api or worker: node scripts/run-backend.mjs api|worker');
  }
  if (mode === 'worker' && extraArguments.length) {
    throw new Error('The processing worker does not accept command-line arguments.');
  }
  const backend = join(project, 'services', 'backend');
  const executable = process.platform === 'win32' ? ['Scripts', 'python.exe'] : ['bin', 'python'];
  const candidates = [join(backend, '.venv', ...executable), join(project, '.venv', ...executable)];
  const python = candidates.find(candidate => {
    try {
      accessSync(candidate, constants.X_OK);
      return true;
    } catch {
      return false;
    }
  });
  if (!python) {
    throw new Error('Backend Python environment is missing. From GuruConnect, run:\n'
      + '  python3.11 -m venv services/backend/.venv\n'
      + '  services/backend/.venv/bin/pip install -r services/backend/requirements.txt');
  }
  return {
    command: python,
    args: mode === 'api'
      ? ['-m', 'uvicorn', 'recorder.main:app', '--host', '127.0.0.1', '--port', '8010', '--no-access-log', ...extraArguments]
      : ['-m', 'recorder.worker'],
    cwd: backend,
  };
}

export function runBackend(mode, extraArguments = [], spawnProcess = spawn) {
  const plan = createLaunchPlan(mode, extraArguments);
  const child = spawnProcess(plan.command, plan.args, { cwd: plan.cwd, stdio: 'inherit', shell: false });
  const handlers = new Map();
  for (const signal of ['SIGINT', 'SIGTERM']) {
    const handler = () => { if (child.exitCode === null && child.signalCode === null) child.kill(signal); };
    handlers.set(signal, handler);
    process.on(signal, handler);
  }
  const cleanup = () => {
    for (const [signal, handler] of handlers) process.off(signal, handler);
  };
  child.once('error', error => {
    cleanup();
    console.error(`Unable to start the ${mode} process (${error.code || 'spawn_failed'}). Check the Python environment.`);
    process.exitCode = 1;
  });
  child.once('exit', (code, signal) => {
    cleanup();
    if (signal) {
      process.exitCode = 128 + (osConstants.signals[signal] || 1);
      process.kill(process.pid, signal);
    } else {
      process.exitCode = code ?? 1;
    }
  });
  return child;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    runBackend(process.argv[2], process.argv.slice(3));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
