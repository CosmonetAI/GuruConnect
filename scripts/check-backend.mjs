import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import { createLaunchPlan } from './run-backend.mjs';
const project = fileURLToPath(new URL('../', import.meta.url));
const { command } = createLaunchPlan('api');
const result = spawnSync(command, ['-m', 'pytest', 'tests/unit', 'tests/backend', '-q', ...process.argv.slice(2)], {
  cwd: project, stdio: 'inherit', env: { ...process.env, PYTHONPATH: join(project, 'services/backend') },
});
process.exit(result.status ?? 1);
