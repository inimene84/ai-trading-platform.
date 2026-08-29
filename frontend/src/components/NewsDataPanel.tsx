import React, { useState, useEffect, useCallback, useRef } from 'react';
import { X, RefreshCw, ExternalLink, TrendingUp, TrendingDown, Minus } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import {
  newsDataService,
  NewsItem,
  FearGreedData,
  EconomicEvent,
  MarketSentimentData,
} from '../services/newsDataService';
import { cn } from '../lib/utils';

interface NewsDataPanelProps {
  isOpen: boolean;
  onClose: () => void;
}

type TabId = 'news' | 'feargreed' | 'calendar' | 'sentiment';

const TABS: { id: TabId; label: string; icon: string }[] = [
  { id: 'news',      label: 'News',      icon: '📰' },
  { id: 'feargreed', label: 'Fear & Greed', icon: '😨' },
  { id: 'calendar',  label: 'Calendar',  icon: '📅' },
  { id: 'sentiment', label: 'Sentiment', icon: '📊' },
];

// ─── Helpers ─────────────────────────────────────────────────────────────────
function timeAgo(dateStr: string): string {
  if (!dateStr) return '';
  try {
    const date = new Date(dateStr);
    const diff = Math.floor((Date.now() - date.getTime()) / 1000);
    if (diff < 60)   return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  } catch {
    return dateStr.slice(0, 16);
  }
}

function SentimentDot({ sentiment }: { sentiment: NewsItem['sentiment'] }) {
  return (
    <span
      className={cn(
        'w-2 h-2 rounded-full flex-shrink-0 mt-1',
        sentiment === 'positive' ? 'bg-emerald-400' :
        sentiment === 'negative' ? 'bg-rose-400' : 'bg-zinc-500'
      )}
    />
  );
}

function Skeleton({ className }: { key?: React.Key; className?: string }) {
  return <div className={cn('animate-pulse rounded bg-zinc-800', className)} />;
}

function getFearGreedColor(value: number): string {
  if (value <= 25)  return 'text-rose-400';
  if (value <= 45)  return 'text-orange-400';
  if (value <= 55)  return 'text-yellow-400';
  if (value <= 75)  return 'text-emerald-400';
  return 'text-green-300';
}

function getFearGreedBg(value: number): string {
  if (value <= 25)  return 'from-rose-500/20 to-rose-500/5';
  if (value <= 45)  return 'from-orange-500/20 to-orange-500/5';
  if (value <= 55)  return 'from-yellow-500/20 to-yellow-500/5';
  if (value <= 75)  return 'from-emerald-500/20 to-emerald-500/5';
  return 'from-green-400/20 to-green-400/5';
}

function getImpactColor(impact: EconomicEvent['impact']): string {
  return impact === 'high' ? 'text-rose-400 bg-rose-400/10 border-rose-400/20'
    : impact === 'medium' ? 'text-amber-400 bg-amber-400/10 border-amber-400/20'
    : 'text-zinc-400 bg-zinc-400/10 border-zinc-400/20';
}

// ─── Tab Contents ─────────────────────────────────────────────────────────────

function NewsTab({ items, loading }: { items: NewsItem[]; loading: boolean }) {
  if (loading) {
    return (
      <div className="flex flex-col gap-3 p-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="flex gap-2">
            <Skeleton className="w-2 h-2 mt-1 flex-shrink-0" />
            <div className="flex-1">
              <Skeleton className="h-3 w-full mb-1" />
              <Skeleton className="h-3 w-3/4 mb-2" />
              <Skeleton className="h-2 w-1/3" />
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (!items.length) {
    return (
      <div className="flex items-center justify-center h-40 text-zinc-500 text-sm">
        No news available
      </div>
    );
  }

  return (
    <div className="flex flex-col divide-y divide-zinc-800/60">
      {items.map((item, i) => (
        <a
          key={i}
          href={item.url}
          target="_blank"
          rel="noopener noreferrer"
          className="flex gap-2.5 p-3 hover:bg-white/5 transition-colors group"
        >
          <SentimentDot sentiment={item.sentiment} />
          <div className="flex-1 min-w-0">
            <p className="text-xs font-medium text-zinc-200 leading-snug group-hover:text-white transition-colors line-clamp-2">
              {item.title}
              <ExternalLink size={10} className="inline ml-1 opacity-0 group-hover:opacity-60 transition-opacity" />
            </p>
            <div className="flex items-center gap-2 mt-1">
              <span className="text-[10px] text-zinc-500 font-mono uppercase tracking-wider">{item.source}</span>
              <span className="text-[10px] text-zinc-600">·</span>
              <span className="text-[10px] text-zinc-600">{timeAgo(item.published)}</span>
            </div>
          </div>
        </a>
      ))}
    </div>
  );
}

function FearGreedTab({ data, loading }: { data: FearGreedData | null; loading: boolean }) {
  if (loading || !data) {
    return (
      <div className="p-4 flex flex-col gap-4">
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-6 w-1/2 mx-auto" />
        <div className="grid grid-cols-7 gap-1">
          {Array.from({ length: 7 }).map((_, i) => <Skeleton key={i} className="h-16" />)}
        </div>
      </div>
    );
  }

  const pct = data.value;
  const colorClass = getFearGreedColor(pct);
  const bgClass = getFearGreedBg(pct);

  return (
    <div className="p-4 flex flex-col gap-4">
      {/* Main gauge card */}
      <div className={cn('rounded-xl border border-zinc-800 bg-gradient-to-b p-6 flex flex-col items-center', bgClass)}>
        <p className="text-xs text-zinc-400 uppercase tracking-widest mb-2">Crypto Fear & Greed Index</p>
        <div className={cn('text-7xl font-black tabular-nums', colorClass)}>{pct}</div>
        <p className={cn('text-lg font-semibold mt-1', colorClass)}>{data.value_classification}</p>

        {/* Gauge bar */}
        <div className="w-full mt-4 bg-gradient-to-r from-rose-500 via-yellow-400 to-emerald-400 rounded-full h-2 relative">
          <div
            className="absolute -top-1 w-3 h-4 bg-white rounded-full shadow-lg transform -translate-x-1/2 border border-zinc-300"
            style={{ left: `${pct}%` }}
          />
        </div>
        <div className="w-full flex justify-between mt-1">
          <span className="text-[9px] text-rose-400 font-mono">Extreme Fear</span>
          <span className="text-[9px] text-yellow-400 font-mono">Neutral</span>
          <span className="text-[9px] text-emerald-400 font-mono">Extreme Greed</span>
        </div>
      </div>

      {/* 7-day history */}
      {data.history.length > 0 && (
        <div>
          <p className="text-[10px] text-zinc-500 uppercase tracking-widest mb-2">7-Day History</p>
          <div className="grid grid-cols-7 gap-1">
            {data.history.slice(0, 7).map((h, i) => {
              const ts = new Date(parseInt(h.timestamp) * 1000);
              const dayLabel = ts.toLocaleDateString('en-US', { weekday: 'short' }).slice(0, 2);
              return (
                <div key={i} className="flex flex-col items-center gap-1">
                  <div
                    className={cn(
                      'w-full rounded text-center text-xs font-bold py-2',
                      h.value <= 25 ? 'bg-rose-500/20 text-rose-400' :
                      h.value <= 45 ? 'bg-orange-500/20 text-orange-400' :
                      h.value <= 55 ? 'bg-yellow-500/20 text-yellow-400' :
                      h.value <= 75 ? 'bg-emerald-500/20 text-emerald-400' :
                                      'bg-green-400/20 text-green-300'
                    )}
                  >
                    {h.value}
                  </div>
                  <span className="text-[9px] text-zinc-600 font-mono">{dayLabel}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Context */}
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-3">
        <p className="text-[11px] text-zinc-400 leading-relaxed">
          {pct <= 25 && 'Markets in extreme fear. Historically a buying opportunity — investors are overly pessimistic.'}
          {pct > 25 && pct <= 45 && 'Fearful market conditions. Caution warranted but potential accumulation zone.'}
          {pct > 45 && pct <= 55 && 'Neutral sentiment. Market participants are balanced between bulls and bears.'}
          {pct > 55 && pct <= 75 && 'Greed is emerging. Markets are bullish but watch for overextension.'}
          {pct > 75 && 'Extreme greed territory. Markets may be overheated — consider taking profits.'}
        </p>
      </div>
    </div>
  );
}

function CalendarTab({ events, loading }: { events: EconomicEvent[]; loading: boolean }) {
  if (loading) {
    return (
      <div className="p-3 flex flex-col gap-2">
        {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-12 w-full" />)}
      </div>
    );
  }

  if (!events.length) {
    return (
      <div className="flex items-center justify-center h-40 text-zinc-500 text-sm">
        No events available
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-gray-950">
          <tr className="border-b border-zinc-800">
            <th className="text-left py-2 px-3 text-[10px] text-zinc-500 font-mono uppercase tracking-wider">Time</th>
            <th className="text-left py-2 px-2 text-[10px] text-zinc-500 font-mono uppercase tracking-wider">Cur</th>
            <th className="text-left py-2 px-2 text-[10px] text-zinc-500 font-mono uppercase tracking-wider">Event</th>
            <th className="text-center py-2 px-2 text-[10px] text-zinc-500 font-mono uppercase tracking-wider">Imp</th>
            <th className="text-right py-2 px-2 text-[10px] text-zinc-500 font-mono uppercase tracking-wider">Fcst</th>
            <th className="text-right py-2 px-3 text-[10px] text-zinc-500 font-mono uppercase tracking-wider">Prev</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-800/40">
          {events.map((ev, i) => (
            <tr key={i} className="hover:bg-white/5 transition-colors">
              <td className="py-2 px-3 font-mono text-[10px] text-zinc-400 whitespace-nowrap">{ev.time}</td>
              <td className="py-2 px-2">
                <span className="text-[10px] font-bold text-zinc-300">{ev.currency}</span>
              </td>
              <td className="py-2 px-2 text-[11px] text-zinc-300 max-w-[130px]">
                <span className="line-clamp-2">{ev.event}</span>
              </td>
              <td className="py-2 px-2 text-center">
                <span className={cn(
                  "text-[9px] font-bold uppercase px-1.5 py-0.5 rounded border",
                  getImpactColor(ev.impact)
                )}>
                  {ev.impact[0].toUpperCase()}
                </span>
              </td>
              <td className="py-2 px-2 text-right font-mono text-[10px] text-zinc-400">{ev.forecast}</td>
              <td className="py-2 px-3 text-right font-mono text-[10px] text-zinc-500">{ev.previous}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SentimentTab({ data, loading }: { data: MarketSentimentData | null; loading: boolean }) {
  if (loading || !data) {
    return (
      <div className="p-4 flex flex-col gap-3">
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  return (
    <div className="p-4 flex flex-col gap-4">
      {/* Bull/Bear/Neutral bars */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
        <p className="text-[10px] text-zinc-500 uppercase tracking-widest mb-3">Market Sentiment</p>
        <div className="flex flex-col gap-2">
          {[
            { label: 'Bullish', pct: data.bull_pct, color: 'bg-emerald-500', textColor: 'text-emerald-400' },
            { label: 'Bearish', pct: data.bear_pct, color: 'bg-rose-500', textColor: 'text-rose-400' },
            { label: 'Neutral', pct: data.neutral_pct, color: 'bg-zinc-500', textColor: 'text-zinc-400' },
          ].map(({ label, pct, color, textColor }) => (
            <div key={label}>
              <div className="flex justify-between mb-1">
                <span className={'text-xs font-medium ' + textColor}>{label}</span>
                <span className={'text-xs font-bold tabular-nums ' + textColor}>{pct.toFixed(1)}%</span>
              </div>
              <div className="h-2 bg-zinc-800 rounded-full overflow-hidden">
                <div
                  className={'h-full rounded-full transition-all duration-700 ' + color}
                  style={{ width: pct + '%' }}
                />
              </div>
            </div>
          ))}
        </div>
        <p className="text-[10px] text-zinc-600 mt-3">{data.total_tracked} assets tracked</p>
      </div>

      {/* Top movers */}
      {data.top_movers.length > 0 && (
        <div>
          <p className="text-[10px] text-zinc-500 uppercase tracking-widest mb-2">Top Movers</p>
          <div className="flex flex-col gap-1">
            {data.top_movers.map((m, i) => (
              <div key={i} className="flex items-center justify-between px-3 py-2 rounded-lg bg-zinc-900/50 border border-zinc-800/60">
                <div className="flex items-center gap-2">
                  {m.direction === 'up'
                    ? <TrendingUp size={12} className="text-emerald-400" />
                    : <TrendingDown size={12} className="text-rose-400" />}
                  <span className="text-xs font-bold text-zinc-200 font-mono">{m.symbol}</span>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-[11px] text-zinc-400 font-mono">
                    ${m.price.toLocaleString(undefined, { maximumFractionDigits: 4 })}
                  </span>
                  <span className={'text-xs font-bold font-mono ' + (m.direction === 'up' ? 'text-emerald-400' : 'text-rose-400')}>
                    {m.change_pct >= 0 ? '+' : ''}{m.change_pct.toFixed(2)}%
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Main Panel Component ─────────────────────────────────────────────────────
export function NewsDataPanel({ isOpen, onClose }: NewsDataPanelProps) {
  const [activeTab, setActiveTab] = useState<TabId>('news');
  const [loading, setLoading] = useState(false);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const [newsItems, setNewsItems] = useState<NewsItem[]>([]);
  const [fearGreed, setFearGreed] = useState<FearGreedData | null>(null);
  const [calendarEvents, setCalendarEvents] = useState<EconomicEvent[]>([]);
  const [sentimentData, setSentimentData] = useState<MarketSentimentData | null>(null);

  const isMounted = useRef(true);
  useEffect(() => {
    isMounted.current = true;
    return () => { isMounted.current = false; };
  }, []);

  const fetchAll = useCallback(async () => {
    if (!isOpen) return;
    setLoading(true);
    try {
      const [news, fg, cal, sent] = await Promise.all([
        newsDataService.getNewsFeed(),
        newsDataService.getFearGreed(),
        newsDataService.getEconomicCalendar(),
        newsDataService.getMarketSentiment(),
      ]);
      if (!isMounted.current) return;
      setNewsItems(news.items);
      setFearGreed(fg);
      setCalendarEvents(cal.events);
      setSentimentData(sent);
      setLastRefresh(new Date());
    } catch (err) {
      console.error('[NewsDataPanel] fetch failed', err);
    } finally {
      if (isMounted.current) setLoading(false);
    }
  }, [isOpen]);

  // Fetch on open
  useEffect(() => {
    if (isOpen) fetchAll();
  }, [isOpen, fetchAll]);

  // Auto-refresh every 60s
  useEffect(() => {
    if (!isOpen) return;
    const interval = setInterval(fetchAll, 60_000);
    return () => clearInterval(interval);
  }, [isOpen, fetchAll]);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  return (
    <>
      {/* Backdrop — clicking it closes the panel */}
      <AnimatePresence>
        {isOpen && (
          <motion.div
            key="news-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-0 z-40 bg-black/20"
            onClick={onClose}
          />
        )}
      </AnimatePresence>

      {/* Panel */}
      <motion.div
        className="fixed right-0 top-0 bottom-0 z-50 w-96 flex flex-col bg-gray-950 border-l border-gray-800 shadow-2xl"
        initial={{ x: 384 }}
        animate={{ x: isOpen ? 0 : 384 }}
        transition={{ type: 'spring', damping: 30, stiffness: 300 }}
        aria-hidden={!isOpen}
      >
        {/* Header */}
        <div className="flex-shrink-0 flex items-center justify-between px-4 py-3 bg-gray-900 border-b border-gray-800">
          <div className="flex items-center gap-2">
            <span className="text-emerald-400 font-mono text-xs font-bold uppercase tracking-widest">Market Intelligence</span>
            {loading && <RefreshCw size={11} className="text-zinc-500 animate-spin" />}
          </div>
          <div className="flex items-center gap-2">
            {lastRefresh && (
              <span className="text-[10px] text-zinc-600 font-mono">
                {lastRefresh.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              </span>
            )}
            <button
              onClick={fetchAll}
              className="p-1.5 text-zinc-500 hover:text-zinc-300 rounded transition-colors"
              title="Refresh"
            >
              <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            </button>
            <button
              onClick={onClose}
              className="p-1.5 text-zinc-500 hover:text-zinc-300 rounded transition-colors"
              title="Close"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Tab bar */}
        <div className="flex-shrink-0 flex gap-1 px-3 py-2 bg-gray-900/80 border-b border-gray-800 overflow-x-auto scrollbar-hide">
          {TABS.map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium whitespace-nowrap transition-all',
                activeTab === tab.id
                  ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                  : 'text-zinc-500 hover:text-zinc-300 hover:bg-white/5'
              )}
            >
              <span>{tab.icon}</span>
              <span>{tab.label}</span>
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto scrollbar-hide">
          {activeTab === 'news'      && <NewsTab items={newsItems} loading={loading && !newsItems.length} />}
          {activeTab === 'feargreed' && <FearGreedTab data={fearGreed} loading={loading && !fearGreed} />}
          {activeTab === 'calendar'  && <CalendarTab events={calendarEvents} loading={loading && !calendarEvents.length} />}
          {activeTab === 'sentiment' && <SentimentTab data={sentimentData} loading={loading && !sentimentData} />}
        </div>
      </motion.div>
    </>
  );
}
