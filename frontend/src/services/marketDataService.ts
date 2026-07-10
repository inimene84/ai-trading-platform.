import { fetchBinance } from './binanceProxy';

type KlineCallback = (candle: any) => void;
type DepthCallback = (depth: any) => void;

export type MarketProvider = 'binance' | 'alphavantage';

class MarketDataService {
  private ws: WebSocket | null = null;
  private klineCallbacks: KlineCallback[] = [];
  private depthCallbacks: DepthCallback[] = [];
  private symbol: string = 'btcusdt';
  private provider: MarketProvider = 'binance';
  private pollInterval: any = null;
  private apiKey: string = '';
  private connectionStatus: 'disconnected' | 'connecting' | 'connected' | 'error' = 'disconnected';
  private statusCallbacks: ((status: string) => void)[] = [];
  private intentionalDisconnect = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  setApiKey(key: string) {
    this.apiKey = key;
  }

  setSymbol(symbol: string, provider: MarketProvider = 'binance') {
    if (this.symbol === symbol && this.provider === provider) return;
    
    this.symbol = symbol.toLowerCase();
    this.provider = provider;
    
    this.disconnect();
    this.connect();
  }

  connect() {
    this.intentionalDisconnect = false;
    if (this.provider === 'binance') {
      this.connectBinance();
    } else if (this.provider === 'alphavantage') {
      this.startAlphaVantagePolling();
    }
  }

  private connectBinance() {
    if (this.ws) return;

    this.connectionStatus = 'connecting';
    this.notifyStatus();
    // Production VPS is geo-blocked by Binance's public WebSocket. Default to
    // the same-origin backend REST feed; direct WS is explicit opt-in only.
    if (import.meta.env.VITE_BINANCE_DIRECT_WS !== 'true') {
      this._startRestFallback();
      this.connectionStatus = 'connected';
      this.notifyStatus();
      return;
    }
    this.ws = new WebSocket(`wss://stream.binance.com:9443/stream?streams=${this.symbol}@kline_1m/${this.symbol}@depth10@100ms`);

    this.ws.onopen = () => {
      this.connectionStatus = 'connected';
      this.notifyStatus();
    };

    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      const stream = data.stream;
      const payload = data.data;

      if (stream.includes('@kline')) {
        const k = payload?.k;
        if (!k) return;
        const candle = {
          time: k.t / 1000,
          open: parseFloat(k.o),
          high: parseFloat(k.h),
          low: parseFloat(k.l),
          close: parseFloat(k.c),
          volume: parseFloat(k.v),
          isFinal: k.x
        };
        this.klineCallbacks.forEach(cb => cb(candle));
      } else if (stream.includes('@depth')) {
        const bids = payload?.b || [];
        const asks = payload?.a || [];
        const depth = {
          bids: bids.map((b: string[]) => [parseFloat(b[0]), parseFloat(b[1])]),
          asks: asks.map((a: string[]) => [parseFloat(a[0]), parseFloat(a[1])])
        };
        this.depthCallbacks.forEach(cb => cb(depth));
      }
    };

    this.ws.onclose = () => {
      if (this.provider === 'binance' && !this.intentionalDisconnect) {
        this.ws = null;
        // If the direct WS never connected (geo-block/CORS), fall back to
        // REST polling through the backend proxy instead of hammering reconnect.
        if (this.connectionStatus !== 'connected') {
          this._startRestFallback();
        } else {
          this.connectionStatus = 'connecting';
          this.notifyStatus();
          this.reconnectTimer = setTimeout(() => this.connect(), 3000);
        }
      }
    };

    this.ws.onerror = () => {
      if (this.intentionalDisconnect) return;
      // stream.binance.com is geo-blocked from some regions; degrade to REST.
      console.warn('Binance WS error — falling back to backend REST polling');
      this.connectionStatus = 'error';
      this.notifyStatus();
      try { this.ws?.close(); } catch { /* ignore */ }
      this.ws = null;
      this._startRestFallback();
    };
  }

  private _startRestFallback() {
    if (this.pollInterval || this.provider !== 'binance') return;
    const sym = this.symbol.toUpperCase();
    const poll = async () => {
      try {
        const [kRes, tRes] = await Promise.all([
          fetchBinance(`https://api.binance.com/api/v3/klines?symbol=${sym}&interval=1m&limit=2`),
          fetchBinance(`https://api.binance.com/api/v3/depth?symbol=${sym}&limit=10`),
        ]);
        if (kRes.ok) {
          const kl = await kRes.json();
          const k = kl[kl.length - 1];
          if (k) {
            this.klineCallbacks.forEach(cb => cb({
              time: k[0] / 1000,
              open: parseFloat(k[1]), high: parseFloat(k[2]),
              low: parseFloat(k[3]), close: parseFloat(k[4]),
              volume: parseFloat(k[5]), isFinal: true,
            }));
          }
        }
        if (tRes.ok) {
          const d = await tRes.json();
          this.depthCallbacks.forEach(cb => cb({
            bids: (d.bids || []).map((b: string[]) => [parseFloat(b[0]), parseFloat(b[1])]),
            asks: (d.asks || []).map((a: string[]) => [parseFloat(a[0]), parseFloat(a[1])]),
          }));
        }
        if (this.connectionStatus !== 'connected') {
          this.connectionStatus = 'connected';
          this.notifyStatus();
        }
      } catch (err) {
        console.warn('REST fallback poll error:', err);
      }
    };
    poll();
    this.pollInterval = setInterval(poll, 3000);
  }

  private notifyStatus() {
    this.statusCallbacks.forEach(cb => cb(this.connectionStatus));
  }

  onStatusChange(cb: (status: string) => void) {
    this.statusCallbacks.push(cb);
    cb(this.connectionStatus);
    return () => {
      this.statusCallbacks = this.statusCallbacks.filter(c => c !== cb);
    };
  }

  getStatus() {
    return this.connectionStatus;
  }

  private async startAlphaVantagePolling() {
    if (this.pollInterval) return;

    const fetchQuote = async () => {
      try {
        // Server-side provider routing keeps API credentials out of the browser.
        const response = await fetch(
          `/api/backend/trading/price?symbol=${encodeURIComponent(this.symbol.toUpperCase())}`,
        );
        if (!response.ok) throw new Error(`price API ${response.status}`);
        const data = await response.json();
        const price = Number(data.price ?? data.current_price ?? data.last);
        if (Number.isFinite(price) && price > 0) {
          // This endpoint is a quote, not OHLC history: render an honest flat
          // snapshot rather than inventing candles or order-book quantities.
          const candle = {
            time: Math.floor(Date.now() / 1000),
            open: price,
            high: price,
            low: price,
            close: price,
            volume: 0,
            isFinal: true,
          };
          this.klineCallbacks.forEach(cb => cb(candle));
        }
      } catch (err) {
        console.error('Alpha Vantage polling error:', err);
      }
    };

    fetchQuote();
    // Alpha Vantage free tier is 5 calls per minute -> 12 seconds
    this.pollInterval = setInterval(fetchQuote, 15000);
  }

  onKline(cb: KlineCallback) {
    this.klineCallbacks.push(cb);
    return () => {
      this.klineCallbacks = this.klineCallbacks.filter(c => c !== cb);
    };
  }

  onDepth(cb: DepthCallback) {
    this.depthCallbacks.push(cb);
    return () => {
      this.depthCallbacks = this.depthCallbacks.filter(c => c !== cb);
    };
  }

  private cachedSymbols: any[] = [];

  async searchSymbols(query: string): Promise<any[]> {
    if (!query || query.length < 2) return [];
    
    if (this.provider === 'binance') {
      try {
        if (this.cachedSymbols.length === 0) {
          const response = await fetchBinance('https://api.binance.com/api/v3/exchangeInfo');
          const data = await response.json();
          this.cachedSymbols = data.symbols.map((s: any) => ({
            symbol: s.symbol,
            name: `${s.baseAsset}/${s.quoteAsset}`,
            type: 'SPOT',
            provider: 'binance'
          }));
        }
        return this.cachedSymbols
          .filter(s => s.symbol.includes(query.toUpperCase()))
          .slice(0, 10);
      } catch (err) {
        console.warn('Failed to fetch Binance symbols, falling back to suggestion:', err);
        return [{ symbol: query.toUpperCase(), name: 'Binance Symbol', provider: 'binance' }];
      }
    } else if (this.provider === 'alphavantage') {
      // Never expose a provider API key in browser query strings. Discovery is
      // local; server-side APIs provide actual prices.
      const topEquities = [
        { symbol: 'AAPL', name: 'Apple Inc.', type: 'Equity', provider: 'alphavantage' },
        { symbol: 'MSFT', name: 'Microsoft Corp.', type: 'Equity', provider: 'alphavantage' },
        { symbol: 'NVDA', name: 'NVIDIA Corp.', type: 'Equity', provider: 'alphavantage' },
        { symbol: 'TSLA', name: 'Tesla Inc.', type: 'Equity', provider: 'alphavantage' },
        { symbol: 'AMZN', name: 'Amazon.com Inc.', type: 'Equity', provider: 'alphavantage' },
        { symbol: 'META', name: 'Meta Platforms Inc.', type: 'Equity', provider: 'alphavantage' },
        { symbol: 'GOOGL', name: 'Alphabet Inc.', type: 'Equity', provider: 'alphavantage' },
      ];
      return topEquities.filter(
        s => s.symbol.includes(query.toUpperCase())
          || s.name.toUpperCase().includes(query.toUpperCase()),
      );
    }
    return [];
  }

  disconnect() {
    this.intentionalDisconnect = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    if (this.pollInterval) {
      clearInterval(this.pollInterval);
      this.pollInterval = null;
    }
  }
}

export const marketDataService = new MarketDataService();
