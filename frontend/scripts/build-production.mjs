import { spawnSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { loadEnv } from 'vite';
import { validateProductionConfig } from './production-config.mjs';

try {
  const environment = { ...loadEnv('production', process.cwd(), ''), ...process.env };
  const config = validateProductionConfig(environment);
  const result = spawnSync(process.execPath, [resolve('node_modules/typescript/bin/tsc')], { stdio: 'inherit', env: environment });
  if (result.status !== 0) process.exit(result.status || 1);
  const build = spawnSync(process.execPath, [resolve('node_modules/vite/bin/vite.js'), 'build'], { stdio: 'inherit', env: environment });
  if (build.status !== 0) process.exit(build.status || 1);
  const index = resolve('dist/index.html');
  const escaped = config.csp.replaceAll('&', '&amp;').replaceAll('"', '&quot;');
  writeFileSync(index, readFileSync(index, 'utf8').replace('<head>', `<head>\n    <meta http-equiv="Content-Security-Policy" content="${escaped}">`));
  console.log('Production build validated: hosted authentication, HTTPS API, and exact-origin content security policy.');
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
