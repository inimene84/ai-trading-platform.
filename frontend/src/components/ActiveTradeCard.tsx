import React, { useEffect, useState, useRef, useCallback } from 'react';
import { X, Edit3, TrendingUp, TrendingDown, Clock, ChevronUp, ChevronDown } from 'lucide-react';

export interface ActiveTrade {
  id: number | string;
  symbol: string;
  side: 'long' | 'short';
  quantity: number;
  entryPrice: number;
  currentPrice: number;
  stopLoss?: number | null;
  takeProfit?: number | null;
  pnl: number;
  pnlPct: number;
  openedAt?: string | null;
  strategy?: string | null;
  reasoning?: string | null;
  broker?: string | null;
}

interface ActiveTradeCardProps {
  trade: ActiveTrade;
  onClose: (tradeId: number | string) => void;
  onModify: (tradeId: number | string, sl: number | null, tp: number | null) => void;
}

function cn(...classes: (string | boolean | undefined | null)[]): string {
  return classes.filter(Boolean).join(' ');
}

function formatPrice(p: number): string {
  if (p >= 10000) return p.toLocaleString('en-US', { maximumFractionDigits: 2 });
  if (p >= 100) return p.toFixed(2);
  if (p >= 1) return p.toFixed(4);
  return p.toFixed(6);
}

function formatPnl(pnl: number): string {
  const abs = Math.abs(pnl);
  if (abs >= 1000) return `${pnl >= 0 ? '+' : '-'}$${abs.toLocaleString('en-US', { maximumFractionDigits: 2 })}`;
  return `${pnl >= 0 ? '+' : '-'}$${abs.toFixed(2)}`;
}

function formatTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('en-US', {
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

function calculateSlProgress(
  side: 'long' | 'short',
  entry: number,
  current: number,
  sl: number | null | undefined,
  tp: number | null | undefined
): number {
  // Returns 0-100 showing how close price is between SL and TP
  if (!sl || !tp) return 50;
  const range = Math.abs(tp - sl);
  if (range === 0) return 50;
  if (side === 'long') {
    return Math.min(100, Math.max(0, ((current - sl) / range) * 100));
  } else {
    return Math.min(100, Math.max(0, ((sl - current) / range) * 100));
  }
}

export function ActiveTradeCard({ trade, onClose, onModify }: ActiveTradeCardProps) {
  const [livePrice, setLivePrice] = useState<number>(trade.currentPrice);
  const [livePnl, setLivePnl] = useState<number>(trade.pnl);
  const [livePnlPct, setLivePnlPct] = useState<number>(trade.pnlPct);
  const [isEditing, setIsEditing] = useState(false);
  const [editSl, setEditSl] = useState<string>(trade.stopLoss ? String(trade.stopLoss) : '');
  const [editTp, setEditTp] = useState<string>(trade.takeProfit ? String(trade.takeProfit) : '');
  const [closing, setClosing] = useState(false);
  const [saving, setSaving] = useState(false);
  const priceRef = useRef<number>(trade.currentPrice);

  const isLong = trade.side === 'long';
  const isPositive = livePnl >= 0;
  const isForex = trade.symbol.endsWith('=X');
  const binanceSym = trade.symbol.replace('=X', '').replace('/', '');

  // Live price polling — backend caches tickers (TICKER_CACHE_TTL_SEC); 15s is enough for P&L cards
  const fetchLivePrice = useCallback(async () => {
    try {
      let p: number | null = null;
      if (isForex) {
        const res = await fetch(
          `/api/backend/trading/price?symbol=${encodeURIComponent(trade.symbol)}`
        );
        if (res.ok) {
          const data = await res.json();
          p = data.price ?? data.current_price ?? data.last ?? null;
        }
      } else {
        // Position P&L must use the same futures mark source as the backend,
        // not Binance spot (basis/funding can make them diverge).
        const res = await fetch(
          `/api/backend/trading/price?symbol=${encodeURIComponent(binanceSym)}`
        );
        if (res.ok) {
          const data = await res.json();
          p = Number(data.price ?? data.current_price ?? data.last);
        }
      }
      if (p !== null && !isNaN(p)) {
        priceRef.current = p;
        setLivePrice(p);
        // Recalculate P&L
        const diff = isLong
          ? p - trade.entryPrice
          : trade.entryPrice - p;
        const pnl = diff * trade.quantity;
        const pnlPct = (diff / trade.entryPrice) * 100;
        setLivePnl(pnl);
        setLivePnlPct(pnlPct);
      }
    } catch (e) {
      // silent fail
    }
  }, [trade.symbol, trade.entryPrice, trade.quantity, isLong, isForex, binanceSym]);

  useEffect(() => {
    fetchLivePrice();
    const iv = setInterval(fetchLivePrice, 15000);
    return () => clearInterval(iv);
  }, [fetchLivePrice]);

  const handleClose = async () => {
    if (closing) return;
    setClosing(true);
    try {
      await onClose(trade.id);
    } finally {
      setClosing(false);
    }
  };

  const handleSaveModify = async () => {
    if (saving) return;
    setSaving(true);
    try {
      const sl = editSl ? parseFloat(editSl) : null;
      const tp = editTp ? parseFloat(editTp) : null;
      await onModify(trade.id, sl, tp);
      setIsEditing(false);
    } finally {
      setSaving(false);
    }
  };

  const slProgress = calculateSlProgress(
    trade.side,
    trade.entryPrice,
    livePrice,
    trade.stopLoss,
    trade.takeProfit
  );

  return (
    <div
      className={cn(
        'relative flex flex-col rounded-xl border overflow-hidden transition-all duration-300',
        'bg-[#141416]',
        isPositive
          ? 'border-emerald-500/20 shadow-emerald-500/5 shadow-md'
          : 'border-rose-500/20 shadow-rose-500/5 shadow-md'
      )}
    >
      {/* Header strip */}
      <div
        className={cn(
          'h-0.5 w-full',
          isPositive
            ? 'bg-gradient-to-r from-emerald-500 to-teal-500'
            : 'bg-gradient-to-r from-rose-500 to-pink-500'
        )}
      />

      <div className="p-4">
        {/* Row 1: Symbol + Direction + Close button */}
        <div className="flex items-start justify-between mb-3">
          <div className="flex items-center gap-2">
            <div
              className={cn(
                'flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-bold uppercase tracking-wider',
                isLong
                  ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                  : 'bg-rose-500/15 text-rose-400 border border-rose-500/30'
              )}
            >
              {isLong ? <TrendingUp size={10} /> : <TrendingDown size={10} />}
              {isLong ? 'LONG' : 'SHORT'}
            </div>
            <div>
              <div className="text-sm font-bold text-white font-mono">{trade.symbol.replace('=X', '').replace('USDT', '/USDT').replace('USDC', '/USDC')}</div>
              {trade.strategy && (
                <div className="text-[9px] text-zinc-500 uppercase tracking-wider">{trade.strategy}</div>
              )}
            </div>
          </div>

          <div className="flex items-center gap-1.5">
            <button
              onClick={() => setIsEditing((v) => !v)}
              className="p-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-400 hover:text-white transition-colors border border-zinc-700"
              title="Modify SL/TP"
            >
              <Edit3 size={11} />
            </button>
            <button
              onClick={handleClose}
              disabled={closing}
              className={cn(
                'px-2.5 py-1.5 rounded-lg text-[10px] font-bold uppercase tracking-wider transition-all border',
                closing
                  ? 'bg-zinc-800 text-zinc-600 border-zinc-700 cursor-not-allowed'
                  : 'bg-rose-500/15 text-rose-400 hover:bg-rose-500/30 border-rose-500/30'
              )}
            >
              {closing ? '...' : 'Close'}
            </button>
          </div>
        </div>

        {/* Row 2: Prices */}
        <div className="grid grid-cols-3 gap-3 mb-3">
          <div>
            <div className="text-[9px] uppercase text-zinc-600 font-bold tracking-wider mb-0.5">Entry</div>
            <div className="text-xs font-mono text-zinc-300">{formatPrice(trade.entryPrice)}</div>
          </div>
          <div>
            <div className="text-[9px] uppercase text-zinc-600 font-bold tracking-wider mb-0.5">Mark</div>
            <div className="text-xs font-mono text-white font-bold">{formatPrice(livePrice)}</div>
          </div>
          <div>
            <div className="text-[9px] uppercase text-zinc-600 font-bold tracking-wider mb-0.5">Qty</div>
            <div className="text-xs font-mono text-zinc-300">{trade.quantity}</div>
          </div>
        </div>

        {/* Row 3: P&L */}
        <div
          className={cn(
            'flex items-center justify-between px-3 py-2 rounded-lg mb-3',
            isPositive ? 'bg-emerald-500/8' : 'bg-rose-500/8'
          )}
        >
          <div className="flex items-center gap-1.5">
            {isPositive ? (
              <ChevronUp size={14} className="text-emerald-400" />
            ) : (
              <ChevronDown size={14} className="text-rose-400" />
            )}
            <span
              className={cn(
                'text-base font-bold font-mono',
                isPositive ? 'text-emerald-400' : 'text-rose-400'
              )}
            >
              {formatPnl(livePnl)}
            </span>
          </div>
          <span
            className={cn(
              'text-sm font-mono font-bold',
              isPositive ? 'text-emerald-300' : 'text-rose-300'
            )}
          >
            {livePnlPct >= 0 ? '+' : ''}{livePnlPct.toFixed(2)}%
          </span>
        </div>

        {/* Row 4: SL / TP Progress Bar */}
        {(trade.stopLoss || trade.takeProfit) && (
          <div className="mb-3">
            <div className="flex items-center justify-between text-[9px] text-zinc-600 font-mono mb-1">
              <span className="text-rose-500/80">SL {trade.stopLoss ? formatPrice(trade.stopLoss) : '—'}</span>
              <span className="text-zinc-500">Price Position</span>
              <span className="text-emerald-500/80">TP {trade.takeProfit ? formatPrice(trade.takeProfit) : '—'}</span>
            </div>
            <div className="relative h-1.5 bg-zinc-800 rounded-full overflow-hidden">
              <div
                className={cn(
                  'absolute left-0 top-0 h-full rounded-full transition-all duration-1000',
                  slProgress > 60
                    ? 'bg-emerald-500'
                    : slProgress > 30
                    ? 'bg-amber-500'
                    : 'bg-rose-500'
                )}
                style={{ width: `${slProgress}%` }}
              />
              {/* Current price marker */}
              <div
                className="absolute top-1/2 -translate-y-1/2 w-2 h-2 rounded-full bg-white border border-zinc-900 shadow"
                style={{ left: `calc(${slProgress}% - 4px)` }}
              />
            </div>
          </div>
        )}

        {/* Edit SL/TP Panel */}
        {isEditing && (
          <div className="mb-3 p-3 bg-zinc-900 rounded-lg border border-zinc-700">
            <div className="text-[9px] uppercase text-zinc-500 font-bold tracking-wider mb-2">Modify SL / TP</div>
            <div className="grid grid-cols-2 gap-2 mb-2">
              <div>
                <label className="text-[9px] text-zinc-600 font-bold uppercase block mb-1">Stop Loss</label>
                <input
                  type="number"
                  step="any"
                  value={editSl}
                  onChange={(e) => setEditSl(e.target.value)}
                  placeholder={trade.stopLoss ? String(trade.stopLoss) : 'None'}
                  className="w-full bg-zinc-800 border border-zinc-700 rounded-lg py-1.5 px-2 text-[11px] font-mono text-white focus:outline-none focus:border-rose-500/50"
                />
              </div>
              <div>
                <label className="text-[9px] text-zinc-600 font-bold uppercase block mb-1">Take Profit</label>
                <input
                  type="number"
                  step="any"
                  value={editTp}
                  onChange={(e) => setEditTp(e.target.value)}
                  placeholder={trade.takeProfit ? String(trade.takeProfit) : 'None'}
                  className="w-full bg-zinc-800 border border-zinc-700 rounded-lg py-1.5 px-2 text-[11px] font-mono text-white focus:outline-none focus:border-emerald-500/50"
                />
              </div>
            </div>
            <div className="flex gap-2">
              <button
                onClick={handleSaveModify}
                disabled={saving}
                className="flex-1 py-1.5 text-[10px] font-bold uppercase rounded-lg bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30 border border-emerald-500/30 transition-colors"
              >
                {saving ? 'Saving...' : 'Save'}
              </button>
              <button
                onClick={() => setIsEditing(false)}
                className="flex-1 py-1.5 text-[10px] font-bold uppercase rounded-lg bg-zinc-800 text-zinc-400 hover:bg-zinc-700 border border-zinc-700 transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {/* Row 5: Timestamp */}
        {trade.openedAt && (
          <div className="flex items-center gap-1 text-[9px] text-zinc-600">
            <Clock size={9} />
            <span>Opened {formatTime(trade.openedAt)}</span>
          </div>
        )}
      </div>
    </div>
  );
}
