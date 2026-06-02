import React, { useState, useEffect, useCallback } from 'react';
import { Play, Pause, RefreshCw, Activity, Clock, Sliders, AlertCircle } from 'lucide-react';
import { apiService } from '../services/apiService';
import { useToast } from './Toast';
import { cn } from '../lib/utils';

export function StrategyControl() {
  const { showToast } = useToast();
  const [intervalMinutes, setIntervalMinutes] = useState<number>(5);
  const [symbolsInput, setSymbolsInput] = useState<string>('');
  const [strategy, setStrategy] = useState<string>('combined');
  const [status, setStatus] = useState<any>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [actionLoading, setActionLoading] = useState<boolean>(false);

  const fetchStatus = useCallback(async () => {
    try {
      const ls = await apiService.getLoopStatus();
      setStatus(ls);
      // Pre-fill inputs on first load if loop is running
      if (ls && ls.running) {
        setIntervalMinutes(ls.interval_minutes);
        setSymbolsInput(ls.symbols ? ls.symbols.join(', ') : '');
        setStrategy(ls.strategy || 'combined');
      }
    } catch (err) {
      console.error('Failed to fetch loop status:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 10000);
    return () => clearInterval(interval);
  }, [fetchStatus]);

  const handleStart = async () => {
    setActionLoading(true);
    try {
      const parsedSymbols = symbolsInput
        .split(',')
        .map((s) => s.trim().toUpperCase())
        .filter(Boolean);

      const payload = {
        interval_minutes: intervalMinutes,
        symbols: parsedSymbols.length > 0 ? parsedSymbols : undefined,
        strategy: strategy,
      };

      const newStatus = await apiService.startLoop(payload);
      setStatus(newStatus);
      showToast('Trading loop started successfully', 'success');
    } catch (err: any) {
      showToast(err.message || 'Failed to start trading loop', 'error');
    } finally {
      setActionLoading(false);
    }
  };

  const handleStop = async () => {
    setActionLoading(true);
    try {
      const newStatus = await apiService.stopLoop();
      setStatus(newStatus);
      showToast('Trading loop stopped successfully', 'info');
    } catch (err: any) {
      showToast(err.message || 'Failed to stop trading loop', 'error');
    } finally {
      setActionLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="bg-[#141416]/60 backdrop-blur-md border border-zinc-800 rounded-2xl p-6 flex items-center justify-center min-h-[300px]">
        <div className="text-center space-y-3">
          <div className="w-8 h-8 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin mx-auto" />
          <p className="text-sm text-zinc-500">Loading loop status…</p>
        </div>
      </div>
    );
  }

  const isRunning = status?.running;

  return (
    <div className={cn(
      "bg-[#141416]/60 backdrop-blur-md border rounded-2xl p-6 space-y-6 transition-all",
      isRunning ? "border-emerald-500/20 shadow-[0_0_15px_rgba(16,185,129,0.05)]" : "border-zinc-800"
    )}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className={cn(
            "p-2.5 rounded-xl flex items-center justify-center",
            isRunning ? "bg-emerald-500/10 text-emerald-400" : "bg-zinc-800 text-zinc-400"
          )}>
            <Activity size={20} className={cn(isRunning && "animate-pulse")} />
          </div>
          <div>
            <h3 className="font-bold text-base flex items-center gap-2">
              Trading Loop Control
              {isRunning ? (
                <span className="flex items-center gap-1 px-2.5 py-0.5 bg-emerald-500/20 text-emerald-400 text-[10px] font-bold rounded-full border border-emerald-500/20">
                  <div className="w-1.5 h-1.5 bg-emerald-400 rounded-full animate-pulse" /> RUNNING
                </span>
              ) : (
                <span className="px-2.5 py-0.5 bg-zinc-800 text-zinc-400 text-[10px] font-bold rounded-full border border-zinc-700">
                  STOPPED
                </span>
              )}
            </h3>
            <p className="text-xs text-zinc-500 mt-0.5">Configure and run execution strategies</p>
          </div>
        </div>
        <button
          onClick={fetchStatus}
          className="p-2 bg-zinc-800 hover:bg-zinc-700 rounded-xl text-zinc-400 hover:text-white transition-colors"
          disabled={actionLoading}
        >
          <RefreshCw size={14} className={actionLoading ? "animate-spin" : ""} />
        </button>
      </div>

      {/* Inputs Form */}
      <div className="space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <label className="text-xs text-zinc-400 font-bold uppercase tracking-wider flex items-center gap-1.5">
              <Clock size={12} /> Interval (Minutes)
            </label>
            <input
              type="number"
              min={1}
              value={intervalMinutes}
              onChange={(e) => setIntervalMinutes(Math.max(1, parseInt(e.target.value) || 1))}
              disabled={isRunning || actionLoading}
              className="w-full bg-zinc-900 border border-zinc-800 focus:border-zinc-700 hover:border-zinc-800 focus:outline-none rounded-xl px-3 py-2.5 text-sm font-mono transition-colors disabled:opacity-50 text-zinc-200"
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-xs text-zinc-400 font-bold uppercase tracking-wider flex items-center gap-1.5">
              <Sliders size={12} /> Strategy
            </label>
            <select
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
              disabled={isRunning || actionLoading}
              className="w-full bg-zinc-900 border border-zinc-800 focus:border-zinc-700 focus:outline-none rounded-xl px-3 py-2.5 text-sm transition-colors disabled:opacity-50 text-zinc-200"
            >
              <option value="combined">Combined (Weighted Voting)</option>
              <option value="trend_following">Trend Following (EMA Crossover)</option>
              <option value="mean_reversion">Mean Reversion (Bollinger + RSI)</option>
              <option value="breakout">Breakout (Donchian Channel)</option>
            </select>
          </div>
        </div>

        <div className="space-y-1.5">
          <label className="text-xs text-zinc-400 font-bold uppercase tracking-wider">
            Symbols (comma-separated)
          </label>
          <input
            type="text"
            placeholder="e.g. BTCUSDT, ETHUSDT, SOLUSDT"
            value={symbolsInput}
            onChange={(e) => setSymbolsInput(e.target.value)}
            disabled={isRunning || actionLoading}
            className="w-full bg-zinc-900 border border-zinc-800 focus:border-zinc-700 hover:border-zinc-800 focus:outline-none rounded-xl px-3 py-2.5 text-sm font-mono transition-colors disabled:opacity-50 text-zinc-200"
          />
          <p className="text-[10px] text-zinc-500">Leave blank to use all default Binance Futures symbols.</p>
        </div>
      </div>

      {/* Action Buttons */}
      <div className="flex gap-3 pt-2">
        {isRunning ? (
          <button
            onClick={handleStop}
            disabled={actionLoading}
            className="flex-1 flex items-center justify-center gap-2 px-5 py-3 bg-rose-500 hover:bg-rose-400 text-white rounded-xl text-sm font-bold transition-all shadow-lg shadow-rose-500/20 disabled:opacity-50 cursor-pointer"
          >
            <Pause size={16} /> Stop Trading Loop
          </button>
        ) : (
          <button
            onClick={handleStart}
            disabled={actionLoading}
            className="flex-1 flex items-center justify-center gap-2 px-5 py-3 bg-emerald-500 hover:bg-emerald-400 text-black rounded-xl text-sm font-bold transition-all shadow-lg shadow-emerald-500/20 disabled:opacity-50 cursor-pointer"
          >
            <Play size={16} fill="currentColor" /> Start Trading Loop
          </button>
        )}
      </div>

      {/* Metrics Footer */}
      {status && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-4 border-t border-zinc-800/60 text-xs">
          <div className="bg-zinc-900/40 p-3 rounded-xl border border-zinc-800">
            <span className="text-zinc-500 block mb-0.5">Cycles Run</span>
            <span className="font-mono font-bold text-sm text-zinc-300">{status.cycle_count ?? 0}</span>
          </div>
          <div className="bg-zinc-900/40 p-3 rounded-xl border border-zinc-800 sm:col-span-2">
            <span className="text-zinc-500 block mb-0.5">Last Cycle Status</span>
            <span className="font-mono font-bold text-sm text-zinc-300 truncate block">
              {status.last_cycle ? new Date(status.last_cycle).toLocaleTimeString() : '—'}
            </span>
          </div>
          {status.next_cycle && (
            <div className="bg-zinc-900/40 p-3 rounded-xl border border-zinc-800 sm:col-span-3">
              <span className="text-zinc-500 block mb-0.5">Next Cycle execution</span>
              <span className="font-mono font-bold text-sm text-zinc-300">
                {new Date(status.next_cycle).toLocaleTimeString()}
              </span>
            </div>
          )}
          {status.error && (
            <div className="sm:col-span-3 flex items-start gap-2 bg-rose-500/5 border border-rose-500/20 p-3 rounded-xl text-rose-400">
              <AlertCircle size={14} className="mt-0.5 flex-shrink-0" />
              <div className="text-[11px] leading-tight">
                <span className="font-bold">Error:</span> {status.error}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
