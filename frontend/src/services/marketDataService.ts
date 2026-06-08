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
      if (this.provider === 'binance') {
        this.ws = null;
        // If the direct WS never connected (geo-block/CORS), fall back to
        // REST polling through the backend proxy instead of hammering reconnect.
        if (this.connectionStatus !== 'connected') {
          this._startRestFallback();
        } else {
          this.connectionStatus = 'connecting';
          this.notifyStatus();
          setTimeout(() => this.connect(), 3000);
        }
      }
    };

    this.ws.onerror = () => {
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
    if (!this.apiKey) {
      console.warn('Alpha Vantage API Key not set');
      return;
    }

    const fetchQuote = async () => {
      try {
        // Global Quote for real-time-ish price
        const response = await fetch(`https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=${this.symbol.toUpperCase()}&apikey=${this.apiKey}`);
        const data = await response.json();
        const quote = data['Global Quote'];
        
        if (quote) {
          const candle = {
            time: Math.floor(Date.now() / 1000),
            open: parseFloat(quote['02. open']),
            high: parseFloat(quote['03. high']),
            low: parseFloat(quote['04. low']),
            close: parseFloat(quote['05. price']),
            volume: parseFloat(quote['06. volume']),
            isFinal: true
          };
          this.klineCallbacks.forEach(cb => cb(candle));
          
          // Alpha Vantage doesn't provide a real-time order book in the free tier
          // We'll simulate a tight spread around the price for the UI
          const price = candle.close;
          const depth = {
            bids: Array.from({ length: 5 }, (_, i) => [price - (i + 1) * 0.01, Math.random() * 100]),
            asks: Array.from({ length: 5 }, (_, i) => [price + (i + 1) * 0.01, Math.random() * 100])
          };
          this.depthCallbacks.forEach(cb => cb(depth));
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
      // If no API key is provided, gracefully downgrade and provide standard equity assets
      if (!this.apiKey) {
        const topEquities = [
          { symbol: 'AAPL', name: 'Apple Inc.', type: 'Equity', provider: 'alphavantage' },
          { symbol: 'MSFT', name: 'Microsoft Corp.', type: 'Equity', provider: 'alphavantage' },
          { symbol: 'NVDA', name: 'NVIDIA Corp.', type: 'Equity', provider: 'alphavantage' },
          { symbol: 'TSLA', name: 'Tesla Inc.', type: 'Equity', provider: 'alphavantage' },
          { symbol: 'AMZN', name: 'Amazon.com Inc.', type: 'Equity', provider: 'alphavantage' },
          { symbol: 'META', name: 'Meta Platforms Inc.', type: 'Equity', provider: 'alphavantage' },
          { symbol: 'GOOGL', name: 'Alphabet Inc.', type: 'Equity', provider: 'alphavantage' }
        ];
        return topEquities.filter(s => s.symbol.includes(query.toUpperCase()) || s.name.toUpperCase().includes(query.toUpperCase()));
      }
      
      try {
        const response = await fetch(`https://www.alphavantage.co/query?function=SYMBOL_SEARCH&keywords=${query}&apikey=${this.apiKey}`);
        const data = await response.json();
        const matches = data['bestMatches'] || [];
        return matches.map((m: any) => ({
          symbol: m['1. symbol'],
          name: m['2. name'],
          type: m['3. type'],
          region: m['4. region'],
          provider: 'alphavantage'
        }));
      } catch (err) {
        console.error('Alpha Vantage search error:', err);
        return [{ symbol: query.toUpperCase(), name: 'Custom Equity Asset', provider: 'alphavantage' }];
      }
    }
    return [];
  }

  disconnect() {
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
