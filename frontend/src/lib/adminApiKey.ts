const LOCAL_STORAGE_KEY = 'quantum_trade_settings';

/**
 * Admin token used by the dashboard.
 * VITE_* values are compiled into the browser bundle — treat them as public.
 * Never add a hardcoded fallback.
 */
export function getAdminApiKey(): string {
  try {
    const envKey = (import.meta as any).env?.VITE_ADMIN_API_KEY;
    if (envKey) return envKey;
    const sessionSecrets = sessionStorage.getItem('quantum_trade_session_secrets');
    if (sessionSecrets) {
      const secrets = JSON.parse(sessionSecrets);
      if (secrets.ADMIN_API_KEY) return secrets.ADMIN_API_KEY;
    }
    const stored = localStorage.getItem(LOCAL_STORAGE_KEY);
    if (stored) {
      const settings = JSON.parse(stored);
      if (settings.ADMIN_API_KEY) return settings.ADMIN_API_KEY;
    }
  } catch {
    /* ignore malformed storage */
  }
  return '';
}
