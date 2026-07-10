/**
 * Binance public-data fetch helper with backend-proxy fallback.
 *
 * The browser calls api.binance.com directly, which fails where Binance is
 * geo-blocked (HTTP 451) or via CORS. When the direct call fails we retry the
 * same request through our backend, which reaches Binance reliably:
 *   https://api.binance.com/api/v3/<path>  ->  <backend>/trading/binance/<path>
 *
 * Once a direct call fails, we remember that for the rest of the session and
 * go straight to the proxy — direct calls poll every few seconds from several
 * components, so retrying a known-blocked host on every tick just doubles
 * request latency and floods the console with CORS/451 noise. Several
 * components also mount at once (one chart per watchlist symbol), so the
 * first probe is shared via `directAccessProbe`: concurrent callers await
 * the same in-flight attempt instead of each firing their own doomed
 * request against api.binance.com.
 */

let directAccessBlocked = false;
let directAccessProbe: Promise<void> | null = null;

function getBackendBase(): string {
  return (import.meta.env.VITE_BACKEND_URL || '/api/backend').replace(/\/+$/, '');
}

function toProxyUrl(url: string): string | null {
  const m = url.match(/api\.binance\.com\/api\/v3\/(.*)$/);
  if (!m) return null;
  return `${getBackendBase()}/trading/binance/${m[1]}`;
}

async function attemptDirect(url: string, init?: RequestInit): Promise<Response | null> {
  try {
    const direct = await fetch(url, init);
    if (direct.ok) return direct;
    // Binance geo-blocks respond 451 without throwing — treat as blocked too.
    if (direct.status === 451) directAccessBlocked = true;
    return null;
  } catch {
    // network/CORS/geo-block — remember it and fall through to the proxy.
    directAccessBlocked = true;
    return null;
  }
}

/**
 * Fetch a Binance public-data URL, falling back to the backend proxy on
 * failure. Accepts the full `https://api.binance.com/api/v3/...` URL so call
 * sites only need to swap `fetch` for `fetchBinance`.
 */
export async function fetchBinance(url: string, init?: RequestInit): Promise<Response> {
  const proxyUrl = toProxyUrl(url);

  if (!directAccessBlocked) {
    if (!directAccessProbe) {
      // This call becomes the shared probe; everyone else below waits for
      // its verdict instead of racing their own doomed request in parallel.
      const probe = attemptDirect(url, init);
      directAccessProbe = probe.then(() => undefined).finally(() => { directAccessProbe = null; });
      const direct = await probe;
      if (direct) return direct;
    } else {
      await directAccessProbe;
      if (!directAccessBlocked) {
        const direct = await attemptDirect(url, init);
        if (direct) return direct;
      }
    }
  }

  if (proxyUrl) return fetch(proxyUrl, init);

  // No rewrite possible; retry the original so the caller sees a real error.
  return fetch(url, init);
}
