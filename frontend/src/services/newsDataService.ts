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

async function fetchWithFallback<T>(url: string, fallback: T): Promise<T> {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(15000) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return (await res.json()) as T;
  } catch (err) {
    console.warn(`[newsDataService] Failed to fetch ${url}:`, err);
    return fallback;
  }
}

export const newsDataService = {
  async getNewsFeed(): Promise<{ items: NewsItem[]; count: number; cached_at: string }> {
    return fetchWithFallback(`${BASE}/feed`, { items: [], count: 0, cached_at: '' });
  },

  async getFearGreed(): Promise<FearGreedData> {
    return fetchWithFallback(`${BASE}/fear-greed`, {
      value: 50,
      value_classification: 'Neutral',
      timestamp: String(Date.now()),
      history: [],
    });
  },

  async getEconomicCalendar(): Promise<{ events: EconomicEvent[] }> {
    return fetchWithFallback(`${BASE}/economic-calendar`, { events: [] });
  },

  async getMarketSentiment(): Promise<MarketSentimentData> {
    return fetchWithFallback(`${BASE}/market-sentiment`, {
      bull_pct: 50,
      bear_pct: 50,
      neutral_pct: 0,
      top_movers: [],
      total_tracked: 0,
    });
  },
};
