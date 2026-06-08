/**
 * Binance public-data fetch helper with backend-proxy fallback.
 *
 * The browser calls api.binance.com directly, which fails where Binance is
 * geo-blocked (HTTP 451) or via CORS. When the direct call fails we retry the
 * same request through our backend, which reaches Binance reliably:
 *   https://api.binance.com/api/v3/<path>  ->  <backend>/trading/binance/<path>
 */

const LOCAL_STORAGE_KEY = 'quantum_trade_settings';

function getBackendBase(): string {
  try {
    const stored = localStorage.getItem(LOCAL_STORAGE_KEY);
    if (stored) {
      const settings = JSON.parse(stored);
      if (settings.BACKEND_URL) return settings.BACKEND_URL.replace(/\/+$/, '');
    }
  } catch { /* ignore */ }
  return '/api/backend';
}

function toProxyUrl(url: string): string | null {
  const m = url.match(/api\.binance\.com\/api\/v3\/(.*)$/);
  if (!m) return null;
  return `${getBackendBase()}/trading/binance/${m[1]}`;
}

/**
 * Fetch a Binance public-data URL, falling back to the backend proxy on
 * failure. Accepts the full `https://api.binance.com/api/v3/...` URL so call
 * sites only need to swap `fetch` for `fetchBinance`.
 */
export async function fetchBinance(url: string, init?: RequestInit): Promise<Response> {
  try {
    const direct = await fetch(url, init);
    if (direct.ok) return direct;
  } catch { /* network/CORS/geo-block — fall through to proxy */ }

  const proxyUrl = toProxyUrl(url);
  if (proxyUrl) return fetch(proxyUrl, init);

  // No rewrite possible; retry the original so the caller sees a real error.
  return fetch(url, init);
}
