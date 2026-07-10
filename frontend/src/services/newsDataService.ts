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
};
