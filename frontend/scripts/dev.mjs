import { spawn } from 'node:child_process';
import { createDevPlans } from './dev-plan.mjs';
const children = [];
let closing = false;
function close(code = 0) {
  if (closing) return;
  closing = true;
  process.exitCode = code;
  for (const child of children) if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM');
}
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => close(signal === 'SIGINT' ? 130 : 143));
try {
  const plans = createDevPlans(process.argv.slice(2));
  console.log('Starting GuruConnect: frontend http://127.0.0.1:5176 · API http://127.0.0.1:8010 · processing inside the API');
  for (const plan of plans) {
    const child = spawn(plan.command, plan.args, { cwd: plan.cwd, stdio: 'inherit', shell: false, env: { ...process.env, ...plan.env } });
    children.push(child);
    child.once('error', () => { console.error('A GuruConnect service could not start. Check npm run setup and available ports.'); close(1); });
    child.once('exit', (code, signal) => { if (!closing) { console.error(`A GuruConnect service stopped (${signal || code}). Stopping the other services.`); close(code || 1); } });
  }
} catch (error) { console.error(error.message); close(1); }
