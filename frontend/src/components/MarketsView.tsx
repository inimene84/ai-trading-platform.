import React, { useState, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import {
  Search, TrendingUp, Star, RefreshCw, Zap, Globe, ArrowUpRight,
  ArrowDownRight, CheckCircle2, AlertCircle, X, ShieldAlert, Sparkles
} from 'lucide-react';
import { cn } from '../lib/utils';
import { useToast } from './Toast';
import { apiService } from '../services/apiService';
import type { FeedQuote } from '../services/apiService';
import { fetchBinance } from '../services/binanceProxy';

interface MarketItem {
  symbol: string;
  baseAsset: string;
  quoteAsset: string;
  price: string;
  numericPrice: number;
  change: string;
  volume: string;
  up: boolean;
  broker: 'binance_futures' | 'ctrader';
  assetClass: 'crypto' | 'forex' | 'metals' | 'indices';
  stale?: boolean;
  source?: string;
}

const METAL_NAMES: Record<string, string> = {
  XAUUSD: 'Gold',
  XAGUSD: 'Silver',
  XPTUSD: 'Platinum',
  XPDUSD: 'Palladium',
};

function quoteToMarketItem(q: FeedQuote): MarketItem {
  const isMetal = q.asset_class === 'metal';
  const isForex = q.asset_class === 'forex';
  const price = q.price ?? 0;
  const changePct = q.change_pct ?? 0;
  const baseAsset =
    METAL_NAMES[q.symbol] ||
    ((isMetal || isForex) && q.symbol.length === 6 ? q.symbol.slice(0, 3) : q.symbol);
  return {
    symbol: q.symbol,
    baseAsset,
    quoteAsset: 'USD',
    price:
      price > 0
        ? price.toLocaleString(undefined, {
            minimumFractionDigits: isForex ? 4 : 2,
            maximumFractionDigits: isForex ? 4 : 2,
          })
        : '—',
    numericPrice: price,
    change: `${changePct >= 0 ? '+' : ''}${changePct.toFixed(2)}%`,
    volume: q.volume != null && q.volume > 0 ? `$${(q.volume / 1e6).toFixed(1)}M` : '—',
    up: changePct >= 0,
    broker: 'ctrader' as const,
    assetClass: isMetal ? ('metals' as const) : isForex ? ('forex' as const) : ('indices' as const),
    stale: q.stale || price <= 0,
    source: q.source,
  };
}

const FALLBACK_MARKETS: MarketItem[] = [
  { symbol: 'EURUSD', baseAsset: 'EUR', quoteAsset: 'USD', price: '1.0854', numericPrice: 1.0854, change: '+0.18%', volume: '$1.2B', up: true, broker: 'ctrader', assetClass: 'forex', stale: true, source: 'placeholder' },
  { symbol: 'GBPUSD', baseAsset: 'GBP', quoteAsset: 'USD', price: '1.2942', numericPrice: 1.2942, change: '-0.12%', volume: '$1.2B', up: false, broker: 'ctrader', assetClass: 'forex', stale: true, source: 'placeholder' },
  { symbol: 'USDJPY', baseAsset: 'USD', quoteAsset: 'JPY', price: '154.22', numericPrice: 154.22, change: '+0.45%', volume: '$1.2B', up: true, broker: 'ctrader', assetClass: 'forex', stale: true, source: 'placeholder' },
  { symbol: 'AUDUSD', baseAsset: 'AUD', quoteAsset: 'USD', price: '0.6534', numericPrice: 0.6534, change: '-0.24%', volume: '$1.2B', up: false, broker: 'ctrader', assetClass: 'forex', stale: true, source: 'placeholder' },
  { symbol: 'USDCAD', baseAsset: 'USD', quoteAsset: 'CAD', price: '1.3812', numericPrice: 1.3812, change: '+0.08%', volume: '$1.2B', up: true, broker: 'ctrader', assetClass: 'forex', stale: true, source: 'placeholder' },
  { symbol: 'USDCHF', baseAsset: 'USD', quoteAsset: 'CHF', price: '0.8845', numericPrice: 0.8845, change: '-0.05%', volume: '$1.2B', up: false, broker: 'ctrader', assetClass: 'forex', stale: true, source: 'placeholder' },
  { symbol: 'NZDUSD', baseAsset: 'NZD', quoteAsset: 'USD', price: '0.5982', numericPrice: 0.5982, change: '-0.31%', volume: '$1.2B', up: false, broker: 'ctrader', assetClass: 'forex', stale: true, source: 'placeholder' },
  { symbol: 'XAUUSD', baseAsset: 'Gold', quoteAsset: 'USD', price: '2,735.60', numericPrice: 2735.6, change: '+0.82%', volume: '$185M', up: true, broker: 'ctrader', assetClass: 'metals', stale: true, source: 'placeholder' },
  { symbol: 'XAGUSD', baseAsset: 'Silver', quoteAsset: 'USD', price: '32.45', numericPrice: 32.45, change: '+1.45%', volume: '$185M', up: true, broker: 'ctrader', assetClass: 'metals', stale: true, source: 'placeholder' },
];

export const MarketsView: React.FC = () => {
  const { showToast } = useToast();
  const [activeTab, setActiveTab] = useState<'all' | 'crypto' | 'forex' | 'metals' | 'indices' | 'watchlist'>('all');
  const [search, setSearch] = useState('');
  const [markets, setMarkets] = useState<MarketItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [feedStale, setFeedStale] = useState(false);
  const [watchlist, setWatchlist] = useState<Set<string>>(new Set(['BTCUSDT', 'EURUSD', 'XAUUSD', 'ETHUSDT']));

  // Order Ticket Modal State
  const [selectedAsset, setSelectedAsset] = useState<MarketItem | null>(null);
  const [orderDirection, setOrderDirection] = useState<'BUY' | 'SELL'>('BUY');
  const [orderQuantity, setOrderQuantity] = useState<number>(0.1);
  const [stopLoss, setStopLoss] = useState<string>('');
  const [takeProfit, setTakeProfit] = useState<string>('');
  const [submittingOrder, setSubmittingOrder] = useState(false);

  const fetchAllMarkets = useCallback(async () => {
    setLoading(true);
    try {
      const items: MarketItem[] = [];

      // 1. Fetch Crypto from Binance
      try {
        const resp = await fetchBinance('https://api.binance.com/api/v3/ticker/24hr');
        const data = await resp.json();
        const cryptoItems: MarketItem[] = data
          .filter((t: any) => (t.symbol.endsWith('USDT') || t.symbol.endsWith('USDC')) && parseFloat(t.quoteVolume) > 2_000_000)
          .slice(0, 20)
          .map((t: any) => ({
            symbol: t.symbol,
            baseAsset: t.symbol.replace('USDT', '').replace('USDC', ''),
            quoteAsset: 'USDT',
            price: parseFloat(t.lastPrice).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 }),
            numericPrice: parseFloat(t.lastPrice),
            change: `${parseFloat(t.priceChangePercent) >= 0 ? '+' : ''}${parseFloat(t.priceChangePercent).toFixed(2)}%`,
            volume: `$${(parseFloat(t.quoteVolume) / 1e6).toFixed(1)}M`,
            up: parseFloat(t.priceChangePercent) >= 0,
            broker: 'binance_futures' as const,
            assetClass: 'crypto' as const,
          }));
        items.push(...cryptoItems);
      } catch (err) {
        console.warn('Binance crypto fetch fallback:', err);
      }

      // 2. Fetch Equities, Forex & Metals from the unified feed
      try {
        const overview = await apiService.getFeedOverview();
        const feedQuotes: FeedQuote[] = [
          ...(overview.equities || []),
          ...(overview.metals || []),
        ];
        try {
          const fx = await apiService.getFeedQuotes(undefined, 'forex');
          feedQuotes.push(...(fx.quotes || []));
        } catch (fxErr) {
          console.warn('Forex quotes fetch fallback:', fxErr);
        }
        items.push(...feedQuotes.map(quoteToMarketItem));
        setFeedStale(false);
      } catch (err) {
        console.warn('Unified feed overview unavailable, using stale placeholders:', err);
        setFeedStale(true);
        items.push(...FALLBACK_MARKETS);
      }

      setMarkets(items);
    } catch (e) {
      console.error('Failed to load market items:', e);
      showToast('Failed to load full market prices', 'error');
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    fetchAllMarkets();
    const interval = setInterval(fetchAllMarkets, 30_000);
    return () => clearInterval(interval);
  }, [fetchAllMarkets]);

  const handleToggleWatchlist = (symbol: string) => {
    setWatchlist(prev => {
      const next = new Set(prev);
      if (next.has(symbol)) {
        next.delete(symbol);
        showToast(`${symbol} removed from watchlist`, 'info');
      } else {
        next.add(symbol);
        showToast(`${symbol} added to watchlist`, 'success');
      }
      return next;
    });
  };

  const handleOpenOrder = (asset: MarketItem) => {
    setSelectedAsset(asset);
    setOrderDirection('BUY');
    setOrderQuantity(asset.broker === 'ctrader' ? (asset.assetClass === 'forex' ? 0.1 : 1.0) : 0.05);
    setStopLoss('');
    setTakeProfit('');
  };

  const handleExecuteSmartOrder = async () => {
    if (!selectedAsset) return;
    setSubmittingOrder(true);
    try {
      const payload = {
        symbol: selectedAsset.symbol,
        direction: orderDirection,
        quantity: Number(orderQuantity),
        price: selectedAsset.numericPrice,
        stop_loss: stopLoss ? parseFloat(stopLoss) : undefined,
        take_profit: takeProfit ? parseFloat(takeProfit) : undefined,
      };
      const res = await apiService.placeSmartOrder(payload);
      if (res && res.success) {
        showToast(
          `Smart order executed on ${res.target_broker.toUpperCase()}: ${orderDirection} ${orderQuantity} ${selectedAsset.symbol}`,
          'success'
        );
        setSelectedAsset(null);
      } else {
        showToast(res?.message || 'Order rejected by broker', 'error');
      }
    } catch (err: any) {
      showToast(err?.message || 'Order placement failed', 'error');
    } finally {
      setSubmittingOrder(false);
    }
  };

  const filteredMarkets = markets.filter(m => {
    const matchesSearch =
      m.symbol.toLowerCase().includes(search.toLowerCase()) ||
      m.baseAsset.toLowerCase().includes(search.toLowerCase());
    if (!matchesSearch) return false;
    if (activeTab === 'all') return true;
    if (activeTab === 'watchlist') return watchlist.has(m.symbol);
    return m.assetClass === activeTab;
  });

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.99 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 1.01 }}
      className="flex-1 overflow-y-auto p-6 flex flex-col gap-6"
    >
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-2xl font-black tracking-tight text-white">QuantumTrade Markets</h2>
            <span className="px-2 py-0.5 rounded-full text-[10px] font-extrabold uppercase bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              Open API Ready
            </span>
          </div>
          <p className="text-xs text-zinc-400 mt-0.5">
            Unified multi-asset feed · Real-time quotes from Binance Futures & cTrader Open API
          </p>
        </div>

        <div className="flex items-center gap-3">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" size={16} />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search markets (BTC, EUR, Gold)..."
              className="w-64 bg-zinc-900/90 border border-zinc-800 rounded-xl py-2 pl-9 pr-4 text-xs font-mono text-white placeholder-zinc-500 focus:outline-none focus:border-emerald-500/50 transition-colors"
            />
          </div>
          <button
            onClick={fetchAllMarkets}
            className="flex items-center gap-2 px-3.5 py-2 bg-zinc-800 hover:bg-zinc-700 rounded-xl text-xs font-bold transition-colors text-zinc-200 border border-zinc-700"
          >
            <RefreshCw size={14} className={cn(loading && "animate-spin text-emerald-400")} /> Refresh
          </button>
        </div>
      </div>

      {/* Market Category Navigation */}
      <div className="flex items-center gap-2 border-b border-zinc-800/80 pb-3 overflow-x-auto">
        {[
          { id: 'all', label: 'All Instruments', count: markets.length },
          { id: 'crypto', label: 'Crypto Perpetuals', count: markets.filter(m => m.assetClass === 'crypto').length, icon: Zap },
          { id: 'forex', label: 'Forex (cTrader)', count: markets.filter(m => m.assetClass === 'forex').length, icon: Globe },
          { id: 'metals', label: 'Metals & CFDs', count: markets.filter(m => m.assetClass === 'metals').length, icon: Sparkles },
          { id: 'indices', label: 'Equities & Indices', count: markets.filter(m => m.assetClass === 'indices').length, icon: TrendingUp },
          { id: 'watchlist', label: 'Watchlist', count: watchlist.size, icon: Star },
        ].map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id as any)}
              className={cn(
                "flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-bold transition-all border",
                activeTab === tab.id
                  ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-400 shadow-sm"
                  : "bg-zinc-900/40 border-zinc-800 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60"
              )}
            >
              {Icon && <Icon size={14} />}
              <span>{tab.label}</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-zinc-800 font-mono text-zinc-400">
                {tab.count}
              </span>
            </button>
          );
        })}
      </div>

      {feedStale && (
        <div className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-400 text-xs font-bold">
          <AlertCircle size={14} />
          Unified feed unavailable — showing placeholder data marked STALE. Retrying every 30s.
        </div>
      )}

      {/* Main Markets Table */}
      <div className="flex-1 bg-[#141416]/90 backdrop-blur-md border border-zinc-800 rounded-2xl overflow-hidden shadow-xl flex flex-col">
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="text-[10px] uppercase tracking-wider text-zinc-500 border-b border-zinc-800/80 bg-zinc-900/40">
                <th className="px-5 py-3.5 w-8"></th>
                <th className="px-5 py-3.5 text-xs font-bold">Instrument</th>
                <th className="px-5 py-3.5 text-xs font-bold">Execution Broker</th>
                <th className="px-5 py-3.5 text-xs font-bold">Last Price</th>
                <th className="px-5 py-3.5 text-xs font-bold">24h Change</th>
                <th className="px-5 py-3.5 text-xs font-bold">Est. 24h Vol</th>
                <th className="px-5 py-3.5 text-right text-xs font-bold">Trade</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/40 text-sm">
              {filteredMarkets.map((m) => (
                <tr
                  key={m.symbol}
                  className="hover:bg-white/[0.03] transition-colors group cursor-pointer"
                  onClick={() => handleOpenOrder(m)}
                >
                  <td
                    className="px-5 py-3.5 text-zinc-600"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleToggleWatchlist(m.symbol);
                    }}
                  >
                    <Star
                      size={15}
                      className={cn(
                        "transition-colors",
                        watchlist.has(m.symbol) ? "fill-amber-400 text-amber-400" : "text-zinc-600 hover:text-amber-300"
                      )}
                    />
                  </td>
                  <td className="px-5 py-3.5">
                    <div className="flex items-center gap-2.5">
                      <div className={cn(
                        "w-8 h-8 rounded-lg flex items-center justify-center font-bold text-xs border",
                        m.broker === 'ctrader'
                          ? "bg-blue-500/10 border-blue-500/20 text-blue-400"
                          : "bg-emerald-500/10 border-emerald-500/20 text-emerald-400"
                      )}>
                        {m.baseAsset.substring(0, 3)}
                      </div>
                      <div>
                        <span className="font-bold text-zinc-100 block leading-tight">
                          {m.baseAsset}
                          {m.stale && (
                            <span className="ml-1.5 px-1 py-0.5 rounded text-[8px] font-black uppercase bg-amber-500/15 text-amber-400 border border-amber-500/30 align-middle">
                              Stale
                            </span>
                          )}
                        </span>
                        <span className="text-[10px] text-zinc-500 font-mono uppercase">
                          {m.baseAsset}/{m.quoteAsset}
                          {m.source && m.source !== 'placeholder' && (
                            <span className="ml-1 text-zinc-600 normal-case">· {m.source}</span>
                          )}
                        </span>
                      </div>
                    </div>
                  </td>
                  <td className="px-5 py-3.5">
                    <span className={cn(
                      "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[10px] font-bold border",
                      m.broker === 'ctrader'
                        ? "bg-sky-500/10 border-sky-500/20 text-sky-400"
                        : "bg-emerald-500/10 border-emerald-500/20 text-emerald-400"
                    )}>
                      {m.broker === 'ctrader' ? <Globe size={11} /> : <Zap size={11} />}
                      {m.broker === 'ctrader' ? 'cTrader Open API' : 'Binance Futures'}
                    </span>
                  </td>
                  <td className="px-5 py-3.5 font-mono font-bold text-zinc-100">
                    {m.assetClass === 'forex' ? '' : '$'}{m.price}
                  </td>
                  <td className={cn(
                    "px-5 py-3.5 font-mono font-bold text-xs inline-flex items-center gap-1 mt-2.5",
                    m.up ? "text-emerald-400" : "text-rose-400"
                  )}>
                    {m.up ? <ArrowUpRight size={14} /> : <ArrowDownRight size={14} />}
                    {m.change}
                  </td>
                  <td className="px-5 py-3.5 font-mono text-zinc-400 text-xs">{m.volume}</td>
                  <td className="px-5 py-3.5 text-right">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleOpenOrder(m);
                      }}
                      className="px-4 py-1.5 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border border-emerald-500/20 rounded-lg text-xs font-bold transition-all shadow-sm group-hover:border-emerald-500/40"
                    >
                      Quick Order
                    </button>
                  </td>
                </tr>
              ))}
              {filteredMarkets.length === 0 && !loading && (
                <tr>
                  <td colSpan={7} className="text-center py-16 text-zinc-500 font-mono text-sm">
                    No instruments match the selected criteria.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Quick Order Modal */}
      <AnimatePresence>
        {selectedAsset && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
            <motion.div
              initial={{ opacity: 0, scale: 0.95, y: 10 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95, y: 10 }}
              className="bg-[#18181b] border border-zinc-700 rounded-2xl w-full max-w-md overflow-hidden shadow-2xl p-6 flex flex-col gap-5"
            >
              <div className="flex items-start justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="text-lg font-black text-white">Smart Order Ticket</h3>
                    <span className={cn(
                      "px-2 py-0.5 rounded text-[10px] font-bold uppercase border",
                      selectedAsset.broker === 'ctrader'
                        ? "bg-sky-500/10 border-sky-500/30 text-sky-400"
                        : "bg-emerald-500/10 border-emerald-500/30 text-emerald-400"
                    )}>
                      {selectedAsset.broker === 'ctrader' ? 'cTrader' : 'Binance'}
                    </span>
                  </div>
                  <p className="text-xs text-zinc-400 mt-0.5">
                    {selectedAsset.baseAsset}/{selectedAsset.quoteAsset} · Spot/Perp Reference: ${selectedAsset.price}
                  </p>
                </div>
                <button
                  onClick={() => setSelectedAsset(null)}
                  className="p-1 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors"
                >
                  <X size={18} />
                </button>
              </div>

              {/* Direction Selector */}
              <div className="grid grid-cols-2 gap-2 bg-zinc-900/80 p-1 rounded-xl border border-zinc-800">
                <button
                  type="button"
                  onClick={() => setOrderDirection('BUY')}
                  className={cn(
                    "py-2.5 rounded-lg text-xs font-black transition-all",
                    orderDirection === 'BUY'
                      ? "bg-emerald-500 text-black shadow-md shadow-emerald-500/20"
                      : "text-zinc-400 hover:text-white"
                  )}
                >
                  BUY (LONG)
                </button>
                <button
                  type="button"
                  onClick={() => setOrderDirection('SELL')}
                  className={cn(
                    "py-2.5 rounded-lg text-xs font-black transition-all",
                    orderDirection === 'SELL'
                      ? "bg-rose-500 text-white shadow-md shadow-rose-500/20"
                      : "text-zinc-400 hover:text-white"
                  )}
                >
                  SELL (SHORT)
                </button>
              </div>

              {/* Order Volume / Lots */}
              <div className="flex flex-col gap-1.5">
                <label className="text-[11px] font-bold text-zinc-400 uppercase tracking-wider">
                  {selectedAsset.broker === 'ctrader' ? 'Volume (Standard Lots, e.g. 0.1 = 10k units)' : 'Quantity (Units / Coin)'}
                </label>
                <input
                  type="number"
                  step={selectedAsset.broker === 'ctrader' ? '0.01' : '0.001'}
                  min="0.001"
                  value={orderQuantity}
                  onChange={(e) => setOrderQuantity(parseFloat(e.target.value) || 0)}
                  className="bg-zinc-900 border border-zinc-800 rounded-xl px-4 py-2.5 text-sm font-mono text-white focus:outline-none focus:border-emerald-500/50"
                />
              </div>

              {/* Risk Controls: SL & TP */}
              <div className="grid grid-cols-2 gap-3">
                <div className="flex flex-col gap-1">
                  <label className="text-[10px] font-bold text-zinc-400 uppercase">Stop Loss (Price)</label>
                  <input
                    type="number"
                    step="any"
                    placeholder="Optional"
                    value={stopLoss}
                    onChange={(e) => setStopLoss(e.target.value)}
                    className="bg-zinc-900 border border-zinc-800 rounded-xl px-3 py-2 text-xs font-mono text-white focus:outline-none focus:border-rose-500/50"
                  />
                </div>
                <div className="flex flex-col gap-1">
                  <label className="text-[10px] font-bold text-zinc-400 uppercase">Take Profit (Price)</label>
                  <input
                    type="number"
                    step="any"
                    placeholder="Optional"
                    value={takeProfit}
                    onChange={(e) => setTakeProfit(e.target.value)}
                    className="bg-zinc-900 border border-zinc-800 rounded-xl px-3 py-2 text-xs font-mono text-white focus:outline-none focus:border-emerald-500/50"
                  />
                </div>
              </div>

              {/* Submit Button */}
              <button
                type="button"
                disabled={submittingOrder || orderQuantity <= 0}
                onClick={handleExecuteSmartOrder}
                className={cn(
                  "w-full py-3 rounded-xl font-black text-sm transition-all flex items-center justify-center gap-2 shadow-lg disabled:opacity-50",
                  orderDirection === 'BUY'
                    ? "bg-emerald-500 hover:bg-emerald-400 text-black shadow-emerald-500/20"
                    : "bg-rose-500 hover:bg-rose-400 text-white shadow-rose-500/20"
                )}
              >
                {submittingOrder ? (
                  <RefreshCw size={16} className="animate-spin" />
                ) : (
                  <CheckCircle2 size={16} />
                )}
                {submittingOrder
                  ? 'Transmitting to Broker...'
                  : `Execute ${orderDirection} ${orderQuantity} ${selectedAsset.symbol}`}
              </button>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </motion.div>
  );
};
