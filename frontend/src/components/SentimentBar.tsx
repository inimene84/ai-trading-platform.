import React, { useEffect, useState } from 'react';
import { newsDataService, PairSentiment } from '../services/newsDataService';
import { TrendingUp, TrendingDown, Minus, RefreshCw } from 'lucide-react';

interface SentimentBarProps {
  symbol: string;
  className?: string;
}

export const SentimentBar: React.FC<SentimentBarProps> = ({ symbol, className = '' }) => {
  const [data, setData] = useState<PairSentiment | null>(null);
  const [loading, setLoading] = useState<boolean>(false);

  const loadSentiment = async () => {
    if (!symbol) return;
    setLoading(true);
    try {
      const res = await newsDataService.getPairSentiment(symbol);
      setData(res);
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadSentiment();
    const interval = setInterval(loadSentiment, 60000); // 1-minute auto-refresh
    return () => clearInterval(interval);
  }, [symbol]);

  if (!data || data.article_count === 0) {
    return (
      <div className={`flex items-center gap-2 text-xs text-zinc-500 bg-zinc-900/50 px-3 py-1.5 rounded-lg border border-zinc-800 ${className}`}>
        <span>Sentiment: Neutral (No recent news)</span>
        {loading && <RefreshCw className="w-3 h-3 animate-spin text-zinc-400" />}
      </div>
    );
  }

  const score = data.recency_weighted_score;
  const isBullish = score > 0.15;
  const isBearish = score < -0.15;

  // Normalized score percentage 0% (full bearish -1.0) to 100% (full bullish +1.0)
  const percent = Math.round(((score + 1.0) / 2.0) * 100);

  return (
    <div className={`flex flex-col gap-1.5 p-3 rounded-lg border border-zinc-800 bg-zinc-900/60 backdrop-blur-sm ${className}`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {isBullish ? (
            <span className="flex items-center gap-1 text-xs font-semibold text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20">
              <TrendingUp className="w-3 h-3" /> BULLISH ({score > 0 ? `+${score.toFixed(2)}` : score.toFixed(2)})
            </span>
          ) : isBearish ? (
            <span className="flex items-center gap-1 text-xs font-semibold text-rose-400 bg-rose-500/10 px-2 py-0.5 rounded border border-rose-500/20">
              <TrendingDown className="w-3 h-3" /> BEARISH ({score.toFixed(2)})
            </span>
          ) : (
            <span className="flex items-center gap-1 text-xs font-medium text-zinc-400 bg-zinc-800 px-2 py-0.5 rounded border border-zinc-700">
              <Minus className="w-3 h-3" /> NEUTRAL ({score.toFixed(2)})
            </span>
          )}
          <span className="text-[11px] text-zinc-400">
            {data.article_count} article{data.article_count > 1 ? 's' : ''} (24h)
          </span>
        </div>
        <button
          onClick={loadSentiment}
          disabled={loading}
          className="text-zinc-500 hover:text-zinc-300 transition-colors"
          title="Refresh Sentiment"
        >
          <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* Visual meter */}
      <div className="relative w-full h-1.5 bg-zinc-800 rounded-full overflow-hidden">
        <div
          className={`h-full transition-all duration-500 rounded-full ${
            isBullish ? 'bg-emerald-500' : isBearish ? 'bg-rose-500' : 'bg-zinc-500'
          }`}
          style={{ width: `${percent}%` }}
        />
      </div>

      <div className="flex justify-between text-[10px] text-zinc-500">
        <span>Bearish (-1.0)</span>
        <span>Confidence: {(data.confidence * 100).toFixed(0)}%</span>
        <span>Bullish (+1.0)</span>
      </div>
    </div>
  );
};
