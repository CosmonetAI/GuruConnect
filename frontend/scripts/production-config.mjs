import { isIP } from 'node:net';

const publicNames = new Set(['VITE_SUPABASE_URL', 'VITE_SUPABASE_PUBLISHABLE_KEY', 'VITE_SUPABASE_ANON_KEY', 'VITE_API_URL', 'VITE_LOCAL_MODE']);

function publicOrigin(value, name) {
  let url;
  try { url = new URL(value); } catch { throw new Error(`${name} must be a public HTTPS origin`); }
  const hostname = url.hostname.toLowerCase();
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash || url.pathname !== '/' || url.port
      || isIP(hostname.replace(/^\[|\]$/g, '')) || !hostname.includes('.')
      || ['localhost', 'local', 'internal', 'test', 'invalid'].some(suffix => hostname === suffix || hostname.endsWith(`.${suffix}`))) {
    throw new Error(`${name} must be a public HTTPS origin without credentials, paths or ports`);
  }
  return url.origin;
}

function publicKey(value) {
  if (/^sb_publishable_[A-Za-z0-9_-]{16,}$/.test(value)) return value;
  try {
    const parts = value.split('.');
    const claims = JSON.parse(Buffer.from(parts[1], 'base64url').toString());
    if (parts.length === 3 && parts.every(part => /^[A-Za-z0-9_-]+$/.test(part)) && claims.role === 'anon') return value;
  } catch { /* Return a safe configuration error; never echo keys. */ }
  throw new Error('The frontend requires a Supabase publishable key or legacy anon key; secret/service-role keys are forbidden');
}

export function validateProductionConfig(environment) {
  for (const name of Object.keys(environment)) {
    if (name.startsWith('VITE_') && !publicNames.has(name) && environment[name]) throw new Error(`Unapproved client environment variable: ${name}`);
  }
  if (environment.VITE_LOCAL_MODE && environment.VITE_LOCAL_MODE !== 'false') throw new Error('Local mode is forbidden in a production release');
  const url = publicOrigin(environment.VITE_SUPABASE_URL, 'VITE_SUPABASE_URL');
  const api = publicOrigin(environment.VITE_API_URL, 'VITE_API_URL');
  const key = publicKey(environment.VITE_SUPABASE_PUBLISHABLE_KEY || environment.VITE_SUPABASE_ANON_KEY || '');
  // Validate both values even when the publishable key takes precedence.
  if (environment.VITE_SUPABASE_ANON_KEY) publicKey(environment.VITE_SUPABASE_ANON_KEY);
  const csp = ["default-src 'self'", "script-src 'self'", "style-src 'self' 'unsafe-inline'", `img-src 'self' ${url} data: blob:`,
    `connect-src 'self' ${url} ${api}`, `media-src 'self' blob: ${url} ${api}`, "worker-src 'self' blob:",
    "object-src 'none'", "base-uri 'none'", "form-action 'self'", 'upgrade-insecure-requests'].join('; ');
  return { supabaseUrl: url, apiUrl: api, key, csp };
}
