import React, { useEffect, useState } from 'react';
import { newsDataService, SymbolBlackoutStatus } from '../services/newsDataService';
import { AlertTriangle, ShieldCheck, Clock, RefreshCw } from 'lucide-react';

interface EventTimelineProps {
  symbol: string;
  className?: string;
}

export const EventTimeline: React.FC<EventTimelineProps> = ({ symbol, className = '' }) => {
  const [blackout, setBlackout] = useState<SymbolBlackoutStatus | null>(null);
  const [loading, setLoading] = useState<boolean>(false);

  const checkStatus = async () => {
    if (!symbol) return;
    setLoading(true);
    try {
      const res = await newsDataService.checkBlackout(symbol);
      setBlackout(res);
    } catch {
      setBlackout(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    checkStatus();
    const interval = setInterval(checkStatus, 30000); // 30s refresh for blackout
    return () => clearInterval(interval);
  }, [symbol]);

  if (!blackout) return null;

  return (
    <div className={`p-3 rounded-lg border bg-zinc-900/60 backdrop-blur-sm ${
      blackout.is_blackout
        ? 'border-rose-500/40 bg-rose-950/20'
        : 'border-zinc-800'
    } ${className}`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {blackout.is_blackout ? (
            <span className="flex items-center gap-1.5 text-xs font-semibold text-rose-400 bg-rose-500/20 px-2 py-0.5 rounded border border-rose-500/30">
              <AlertTriangle className="w-3.5 h-3.5" /> EVENT BLACKOUT ACTIVE
            </span>
          ) : (
            <span className="flex items-center gap-1.5 text-xs font-medium text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20">
              <ShieldCheck className="w-3.5 h-3.5" /> Macro Gate Clear
            </span>
          )}
          <span className="text-[11px] text-zinc-400">
            {blackout.symbol}
          </span>
        </div>
        <button
          onClick={checkStatus}
          disabled={loading}
          className="text-zinc-500 hover:text-zinc-300 transition-colors"
          title="Check Blackout Status"
        >
          <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      <div className="mt-1.5 text-xs text-zinc-300">
        {blackout.reason}
      </div>

      {blackout.active_event && (
        <div className="mt-2 flex items-center justify-between text-[11px] bg-zinc-800/60 px-2.5 py-1.5 rounded border border-zinc-700/50">
          <div className="flex items-center gap-1.5">
            <span className="px-1.5 py-0.2 rounded bg-rose-500/20 text-rose-300 text-[10px] font-bold">
              {blackout.active_event.impact}
            </span>
            <span className="text-zinc-200 font-medium">
              [{blackout.active_event.currency}] {blackout.active_event.title}
            </span>
          </div>
          <div className="flex items-center gap-1 text-zinc-400">
            <Clock className="w-3 h-3" />
            <span>{new Date(blackout.active_event.time_utc).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} UTC</span>
          </div>
        </div>
      )}
    </div>
  );
};
