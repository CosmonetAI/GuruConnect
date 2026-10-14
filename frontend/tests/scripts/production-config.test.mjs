import assert from 'node:assert/strict';
import test from 'node:test';
import { validateProductionConfig } from '../../scripts/production-config.mjs';

const environment = () => ({ VITE_SUPABASE_URL: 'https://fixture.supabase.co', VITE_API_BASE_URL: 'https://fixture-api.onrender.com',
  VITE_SUPABASE_PUBLISHABLE_KEY: 'sb_publishable_synthetic_fixture_only' });
const jwt = role => ['header', Buffer.from(JSON.stringify({ role })).toString('base64url'), 'signature'].join('.');
test('release pins API and Supabase origins in CSP and accepts public legacy keys', () => {
  const result = validateProductionConfig(environment());
  assert.ok(result.csp.includes('connect-src \'self\' https://fixture.supabase.co https://fixture-api.onrender.com'));
  assert.ok(!result.csp.includes('https:;'));
  assert.equal(validateProductionConfig({ ...environment(), VITE_SUPABASE_PUBLISHABLE_KEY: '', VITE_SUPABASE_ANON_KEY: jwt('anon') }).key, jwt('anon'));
});
for (const url of ['http://api.onrender.com', 'https://localhost', 'https://127.0.0.1', 'https://[::1]', 'https://api.internal', 'https://user:secret@api.onrender.com', 'https://api.onrender.com/path', 'https://api.onrender.com?key=private', 'https://api.onrender.com:1234']) {
  test(`release rejects unsafe API origin ${url.split('@').at(-1)}`, () => assert.throws(() => validateProductionConfig({ ...environment(), VITE_API_BASE_URL: url })));
}
test('release rejects local fallback, unapproved browser variables and privileged keys', () => {
  assert.throws(() => validateProductionConfig({ ...environment(), VITE_LOCAL_MODE: 'true' }));
  assert.throws(() => validateProductionConfig({ ...environment(), VITE_PROVIDER_API_KEY: 'fixture-only' }));
  for (const key of ['sb_secret_synthetic_fixture', jwt('service_role'), '']) assert.throws(() => validateProductionConfig({ ...environment(), VITE_SUPABASE_PUBLISHABLE_KEY: key }));
  assert.throws(() => validateProductionConfig({ ...environment(), VITE_SUPABASE_ANON_KEY: jwt('service_role') }));
});
