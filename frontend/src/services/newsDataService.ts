/**
 * News & Market Data Service
 * Connects to the backend /api/news/* endpoints
 */

const BASE = '/api/news';

export interface NewsItem {
  title: string;
  summary: string;
  url: string;
  source: string;
  published: string;
  sentiment: 'positive' | 'negative' | 'neutral';
}

export interface FearGreedData {
  value: number;
  value_classification: string;
  timestamp: string;
  history: Array<{
    value: number;
    value_classification: string;
    timestamp: string;
  }>;
}

export interface EconomicEvent {
  time: string;
  currency: string;
  event: string;
  impact: 'high' | 'medium' | 'low';
  forecast: string;
  previous: string;
}

export interface MarketMover {
  symbol: string;
  price: number;
  change_pct: number;
  direction: 'up' | 'down';
}

export interface MarketSentimentData {
  bull_pct: number;
  bear_pct: number;
  neutral_pct: number;
  top_movers: MarketMover[];
  total_tracked: number;
}

async function fetchData<T>(url: string): Promise<T> {
  const res = await fetch(url, { signal: AbortSignal.timeout(15000) });
  if (!res.ok) throw new Error(`News API ${res.status}`);
  return (await res.json()) as T;
}

export interface PairSentiment {
  pair: string;
  article_count: number;
  avg_score: number;
  recency_weighted_score: number;
  signal: 'bullish' | 'bearish' | 'neutral';
  confidence: number;
  last_updated: string | null;
}

export interface SymbolBlackoutStatus {
  symbol: string;
  is_blackout: boolean;
  reason: string;
  active_event?: {
    id: number;
    title: string;
    currency: string;
    time_utc: string;
    impact: string;
  } | null;
}

export const newsDataService = {
  async getNewsFeed(): Promise<{ items: NewsItem[]; count: number; cached_at: string }> {
    return fetchData(`${BASE}/feed`);
  },

  async getFearGreed(): Promise<FearGreedData> {
    return fetchData(`${BASE}/fear-greed`);
  },

  async getEconomicCalendar(): Promise<{ events: EconomicEvent[] }> {
    return fetchData(`${BASE}/economic-calendar`);
  },

  async getMarketSentiment(): Promise<MarketSentimentData> {
    return fetchData(`${BASE}/market-sentiment`);
  },

  async getPairSentiment(pair: string, windowHours: number = 24): Promise<PairSentiment> {
    const res = await fetchData<{ status: string; sentiment: PairSentiment }>(
      `/api/sentiment/${pair.toUpperCase()}?window_hours=${windowHours}`
    );
    return res.sentiment;
  },

  async getAllPairSentiment(windowHours: number = 24): Promise<PairSentiment[]> {
    const res = await fetchData<{ status: string; pairs: PairSentiment[] }>(
      `/api/sentiment?window_hours=${windowHours}`
    );
    return res.pairs;
  },

  async checkBlackout(symbol: string): Promise<SymbolBlackoutStatus> {
    return fetchData<SymbolBlackoutStatus>(`/api/calendar/check/${symbol.toUpperCase()}`);
  },

  async getMacroEvents(currency?: string, hoursAhead: number = 24): Promise<{ events: any[]; is_stale: boolean }> {
    const q = currency ? `?currency=${encodeURIComponent(currency)}&hours_ahead=${hoursAhead}` : `?hours_ahead=${hoursAhead}`;
    return fetchData<{ events: any[]; is_stale: boolean }>(`/api/calendar${q}`);
  },
};

