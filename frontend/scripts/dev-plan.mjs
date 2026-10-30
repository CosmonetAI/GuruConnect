import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { createLaunchPlan } from './run-backend.mjs';

const defaultProject = fileURLToPath(new URL('../', import.meta.url));

export function createDevPlans(extraArguments = [], project = defaultProject) {
  const backend = resolve(project, '..', 'backend');
  return [
    { ...createLaunchPlan('api', ['--reload', '--reload-dir', resolve(backend, 'recorder'), '--reload-dir', resolve(backend, 'app')], project),
      // iCloud/File Provider emits native change events while hydrating imports.
      // Poll file contents/mtimes so reading a cloud-managed checkout cannot
      // repeatedly restart the API during its own startup.
      env: { WATCHFILES_FORCE_POLLING: 'true', WATCHFILES_POLL_DELAY_MS: '1000' } },
    { command: process.execPath, args: [resolve(project, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', '5176', '--strictPort', ...extraArguments], cwd: project },
  ];
}
