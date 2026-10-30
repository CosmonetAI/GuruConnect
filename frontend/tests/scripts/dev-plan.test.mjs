import test from 'node:test';
import assert from 'node:assert/strict';
import { createDevPlans } from '../../scripts/dev-plan.mjs';
import { createLaunchPlan } from '../../scripts/run-backend.mjs';

test('combined development server reloads only backend application sources', () => {
  const [api, web] = createDevPlans(['--port', '5180']);
  assert.ok(api.args.includes('--reload'));
  assert.equal(api.env.WATCHFILES_FORCE_POLLING, 'true');
  const directories = api.args.filter((_, index) => api.args[index - 1] === '--reload-dir');
  assert.equal(directories.length, 2);
  assert.ok(directories.some(directory => directory.endsWith('/backend/recorder')));
  assert.ok(directories.some(directory => directory.endsWith('/backend/app')));
  assert.deepEqual(web.args.slice(-2), ['--port', '5180']);
});

test('standalone API launch remains unchanged unless reload is requested', () => {
  assert.equal(createLaunchPlan('api').args.includes('--reload'), false);
});
