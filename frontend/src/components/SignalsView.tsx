import React, { useState, useEffect, useCallback } from 'react';
import { motion } from 'motion/react';
import {
  Activity, TrendingUp, TrendingDown, Clock, Shield, Sparkles, RefreshCw,
  ChevronDown, ChevronUp, Filter, ArrowUpRight, ArrowDownRight, Minus
} from 'lucide-react';
import { cn } from '../lib/utils';
import { apiService, Signal } from '../services/apiService';
import { useToast } from './Toast';

const DirectionBadge = ({ direction }: { direction: string }) => {
  const d = direction.toUpperCase();
  if (d === 'BUY' || d === 'BULLISH' || d === 'LONG') {
    return (
      <span className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-emerald-500/10 text-emerald-400 text-[10px] font-bold uppercase tracking-wider border border-emerald-500/20">
        <ArrowUpRight size={12} /> {d}
      </span>
    );
  }
  if (d === 'SELL' || d === 'BEARISH' || d === 'SHORT') {
    return (
      <span className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-rose-500/10 text-rose-400 text-[10px] font-bold uppercase tracking-wider border border-rose-500/20">
        <ArrowDownRight size={12} /> {d}
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-zinc-800 text-zinc-400 text-[10px] font-bold uppercase tracking-wider border border-zinc-700">
      <Minus size={12} /> {d || 'HOLD'}
    </span>
  );
};

const ConfidenceBar = ({ value }: { value: number }) => {
  const pct = Math.round(value * 100);
  const color = pct >= 70 ? 'bg-emerald-500' : pct >= 50 ? 'bg-amber-500' : 'bg-rose-500';
  return (
    <div className="flex items-center gap-2 min-w-[120px]">
      <div className="flex-1 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
        <div className={cn("h-full rounded-full transition-all duration-700", color)} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] font-mono font-bold text-zinc-400 w-8 text-right">{pct}%</span>
    </div>
  );
};

const StatusBadge = ({ status }: { status: string }) => {
  const s = status.toLowerCase();
  const map: Record<string, { bg: string; text: string }> = {
    executed: { bg: 'bg-emerald-500/10 border-emerald-500/20', text: 'text-emerald-400' },
    approved: { bg: 'bg-sky-500/10 border-sky-500/20', text: 'text-sky-400' },
    evaluated: { bg: 'bg-zinc-800 border-zinc-700', text: 'text-zinc-400' },
    pending: { bg: 'bg-amber-500/10 border-amber-500/20', text: 'text-amber-400' },
    rejected: { bg: 'bg-rose-500/10 border-rose-500/20', text: 'text-rose-400' },
    skipped: { bg: 'bg-amber-500/10 border-amber-500/20', text: 'text-amber-400' },
    expired: { bg: 'bg-zinc-800 border-zinc-700', text: 'text-zinc-500' },
    ai_analyzed: { bg: 'bg-indigo-500/10 border-indigo-500/20', text: 'text-indigo-400' },
  };
  const style = map[s] || map.pending;
  return (
    <span className={cn("px-2 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider border", style.bg, style.text)}>
      {status}
    </span>
  );
};

export const SignalsView = () => {
  const { showToast } = useToast();
  const [signals, setSignals] = useState<Signal[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [filterStrategy, setFilterStrategy] = useState<string>('');
  const [autoRefresh, setAutoRefresh] = useState(true);

  const fetchSignals = useCallback(async () => {
    try {
      const res = await apiService.getSignalsHistory({ limit: 50 });
      setSignals(res.signals || []);
    } catch (err) {
      console.warn('Failed to fetch signals:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSignals();
  }, [fetchSignals]);

  useEffect(() => {
    if (!autoRefresh) return;
    const iv = setInterval(fetchSignals, 15_000);
    return () => clearInterval(iv);
  }, [autoRefresh, fetchSignals]);

  const filteredSignals = filterStrategy
    ? signals.filter(s => s.strategy === filterStrategy)
    : signals;

  const strategies = [...new Set(signals.map(s => s.strategy).filter(Boolean))];

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -10 }}
      className="flex-1 overflow-y-auto p-6 space-y-6"
    >
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">AI Trading Signals</h2>
          <p className="text-sm text-zinc-400">Real-time signals from the multi-agent analysis pipeline</p>
        </div>
        <div className="flex items-center gap-3">
          {/* Strategy Filter */}
          {strategies.length > 0 && (
            <div className="relative">
              <Filter size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
              <select
                aria-label="Filter signals by strategy"
                value={filterStrategy}
                onChange={(e) => setFilterStrategy(e.target.value)}
                className="bg-zinc-900 border border-zinc-800 rounded-xl py-2 pl-9 pr-4 text-xs font-medium focus:outline-none focus:border-emerald-500/50 appearance-none min-w-[150px]"
              >
                <option value="">All Strategies</option>
                {strategies.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
          )}

          {/* Auto-refresh toggle */}
          <button
            onClick={() => setAutoRefresh(!autoRefresh)}
            className={cn(
              "flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-bold transition-all border",
              autoRefresh
                ? "bg-emerald-500/10 border-emerald-500/20 text-emerald-400"
                : "bg-zinc-900 border-zinc-800 text-zinc-500"
            )}
          >
            <RefreshCw size={14} className={cn(autoRefresh && "animate-spin")} style={{ animationDuration: '3s' }} />
            {autoRefresh ? 'Live' : 'Paused'}
          </button>

          <button
            onClick={() => { setLoading(true); fetchSignals(); showToast('Signals refreshed', 'info'); }}
            className="flex items-center gap-2 px-4 py-2 bg-zinc-800 hover:bg-zinc-700 rounded-xl text-xs font-bold transition-colors"
          >
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
      </div>

      {/* Signals List */}
      {loading && signals.length === 0 ? (
        <div className="flex-1 flex items-center justify-center py-20">
          <div className="text-center space-y-3">
            <div className="w-8 h-8 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin mx-auto" />
            <p className="text-sm text-zinc-500">Loading signals from backend…</p>
          </div>
        </div>
      ) : filteredSignals.length === 0 ? (
        <div className="flex-1 flex flex-col items-center justify-center py-20 bg-[#141416] border border-zinc-800 border-dashed rounded-3xl space-y-4">
          <div className="w-16 h-16 bg-zinc-900 rounded-2xl flex items-center justify-center text-zinc-700">
            <Activity size={32} />
          </div>
          <div className="text-center">
            <h3 className="text-lg font-bold">No Signals Yet</h3>
            <p className="text-zinc-500 text-sm max-w-md mx-auto">
              Start the trading loop or run an on-demand analysis to generate AI trading signals.
            </p>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          {filteredSignals.map((signal) => (
            <motion.div
              key={signal.id}
              layout
              initial={{ opacity: 0, y: 5 }}
              animate={{ opacity: 1, y: 0 }}
              className="bg-[#141416] border border-zinc-800 rounded-2xl overflow-hidden hover:border-zinc-700 transition-colors"
            >
              {/* Signal Row */}
              <div
                className="flex items-center gap-6 px-6 py-4 cursor-pointer"
                onClick={() => setExpandedId(expandedId === signal.id ? null : signal.id)}
              >
                <div className="flex items-center gap-3 min-w-[140px]">
                  <div className={cn(
                    "w-10 h-10 rounded-xl flex items-center justify-center",
                    signal.direction.toUpperCase().includes('BUY') ? "bg-emerald-500/10" :
                    signal.direction.toUpperCase().includes('SELL') ? "bg-rose-500/10" : "bg-zinc-800"
                  )}>
                    {signal.direction.toUpperCase().includes('BUY') ? (
                      <TrendingUp size={20} className="text-emerald-400" />
                    ) : signal.direction.toUpperCase().includes('SELL') ? (
                      <TrendingDown size={20} className="text-rose-400" />
                    ) : (
                      <Minus size={20} className="text-zinc-500" />
                    )}
                  </div>
                  <div>
                    <p className="text-sm font-bold">{signal.symbol}</p>
                    <p className="text-[10px] text-zinc-500 font-mono">{signal.strategy}</p>
                  </div>
                </div>

                <DirectionBadge direction={signal.direction} />
                <ConfidenceBar value={signal.confidence || 0} />

                <div className="flex items-center gap-6 flex-1 justify-end">
                  {signal.entry_price && (
                    <div className="text-right">
                      <p className="text-[9px] text-zinc-500 uppercase tracking-wider">Entry</p>
                      <p className="text-xs font-mono font-bold">${signal.entry_price.toLocaleString()}</p>
                    </div>
                  )}
                  {signal.stop_loss && (
                    <div className="text-right">
                      <p className="text-[9px] text-zinc-500 uppercase tracking-wider">SL</p>
                      <p className="text-xs font-mono text-rose-400">${signal.stop_loss.toLocaleString()}</p>
                    </div>
                  )}
                  {signal.take_profit && (
                    <div className="text-right">
                      <p className="text-[9px] text-zinc-500 uppercase tracking-wider">TP</p>
                      <p className="text-xs font-mono text-emerald-400">${signal.take_profit.toLocaleString()}</p>
                    </div>
                  )}
                  <StatusBadge status={signal.status} />
                  <div className="text-zinc-500">
                    {expandedId === signal.id ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                  </div>
                </div>
              </div>

              {/* Expanded Detail */}
              {expandedId === signal.id && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  className="border-t border-zinc-800 px-6 py-4 bg-zinc-900/30"
                >
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <div>
                      <p className="text-[10px] uppercase text-zinc-500 font-bold tracking-wider mb-2">AI Reasoning</p>
                      <p className="text-xs text-zinc-300 leading-relaxed">
                        {signal.reasoning || 'No reasoning provided for this signal.'}
                      </p>
                    </div>
                    <div className="space-y-3">
                      <div>
                        <p className="text-[10px] uppercase text-zinc-500 font-bold tracking-wider mb-2">Signal Details</p>
                        <div className="grid grid-cols-2 gap-3 text-xs">
                          <div className="bg-zinc-800/50 rounded-lg p-3">
                            <span className="text-zinc-500">Generated</span>
                            <p className="font-mono text-white mt-1">
                              {signal.timestamp ? new Date(signal.timestamp).toLocaleString() : 'N/A'}
                            </p>
                          </div>
                          <div className="bg-zinc-800/50 rounded-lg p-3">
                            <span className="text-zinc-500">Signal ID</span>
                            <p className="font-mono text-white mt-1">#{signal.id}</p>
                          </div>
                        </div>
                      </div>
                      {signal.ai_analysis && (
                        <div>
                          <p className="text-[10px] uppercase text-zinc-500 font-bold tracking-wider mb-2">AI Analysis Keys</p>
                          <div className="flex flex-wrap gap-1.5">
                            {Object.keys(signal.ai_analysis).map(key => (
                              <span key={key} className="px-2 py-0.5 bg-indigo-500/10 border border-indigo-500/20 text-indigo-300 text-[9px] font-mono rounded">
                                {key}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                </motion.div>
              )}
            </motion.div>
          ))}
        </div>
      )}
    </motion.div>
  );
};
