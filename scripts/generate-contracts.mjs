import { existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';
const python = process.env.GURU_PYTHON || ['.venv/bin/python', 'services/backend/.venv/bin/python'].find(existsSync) || 'python3.11';
const generation = spawnSync(python, ['scripts/generate_contracts.py'], { stdio: 'inherit' });
if (generation.status !== 0) process.exit(generation.status || 1);
const types = spawnSync(process.execPath, [resolve('node_modules/openapi-typescript/bin/cli.js'), 'packages/contracts/openapi.json', '-o', 'packages/contracts/api.generated.ts'], { stdio: 'inherit' });
process.exit(types.status || 0);
