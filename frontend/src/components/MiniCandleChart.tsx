import React, { useEffect, useRef, useState, useCallback } from 'react';

interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface MiniCandleChartProps {
  key?: string | number;
  symbol: string;
  displayName: string;
  signal?: 'BUY' | 'SELL' | 'HOLD' | null;
  isSelected?: boolean;
  onSelect?: (symbol: string) => void;
  onQuickTrade?: (symbol: string, side: 'buy' | 'sell') => void;
}

const CHART_H = 72;
const CHART_W = 180;
const CANDLE_COUNT = 30;

function cn(...classes: (string | boolean | undefined | null)[]): string {
  return classes.filter(Boolean).join(' ');
}

export function MiniCandleChart({
  symbol,
  displayName,
  signal,
  isSelected,
  onSelect,
  onQuickTrade,
}: MiniCandleChartProps) {
  const [candles, setCandles] = useState<Candle[]>([]);
  const [currentPrice, setCurrentPrice] = useState<number | null>(null);
  const [change24h, setChange24h] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const isForex = symbol.endsWith('=X');
  const binanceSymbol = symbol.replace('/', '');

  const fetchData = useCallback(async () => {
    try {
      if (isForex) {
        // Fetch forex from backend
        const res = await fetch(`/api/backend/trading/price?symbol=${encodeURIComponent(symbol)}`);
        if (!res.ok) throw new Error('Forex fetch failed');
        const data = await res.json();
        const p = data.price ?? data.current_price ?? data.last ?? null;
        if (p !== null) setCurrentPrice(Number(p));
        setChange24h(data.change_pct ?? null);
        // Generate synthetic candles from price if no OHLCV available
        if (data.candles && Array.isArray(data.candles)) {
          setCandles(data.candles.slice(-CANDLE_COUNT).map((c: any) => ({
            time: c.time ?? c[0],
            open: Number(c.open ?? c[1]),
            high: Number(c.high ?? c[2]),
            low: Number(c.low ?? c[3]),
            close: Number(c.close ?? c[4]),
            volume: Number(c.volume ?? c[5] ?? 0),
          })));
        } else if (p !== null) {
          // Generate synthetic candles with small noise
          const base = Number(p);
          const synthetic: Candle[] = Array.from({ length: CANDLE_COUNT }, (_, i) => {
            const noise = (Math.random() - 0.5) * base * 0.002;
            const o = base + noise;
            const c = base + (Math.random() - 0.5) * base * 0.002;
            return {
              time: Date.now() - (CANDLE_COUNT - i) * 60000,
              open: o,
              high: Math.max(o, c) + Math.abs(noise) * 0.5,
              low: Math.min(o, c) - Math.abs(noise) * 0.5,
              close: c,
              volume: 0,
            };
          });
          setCandles(synthetic);
        }
        setError(false);
      } else {
        // Fetch crypto candles from Binance
        const [klinesRes, tickerRes] = await Promise.all([
          fetch(
            `https://api.binance.com/api/v3/klines?symbol=${binanceSymbol}&interval=1m&limit=${CANDLE_COUNT}`
          ),
          fetch(
            `https://api.binance.com/api/v3/ticker/24hr?symbol=${binanceSymbol}`
          ),
        ]);

        if (!klinesRes.ok || !tickerRes.ok) throw new Error('Binance fetch failed');

        const klines = await klinesRes.json();
        const ticker = await tickerRes.json();

        const parsedCandles: Candle[] = klines.map((k: any[]) => ({
          time: k[0],
          open: parseFloat(k[1]),
          high: parseFloat(k[2]),
          low: parseFloat(k[3]),
          close: parseFloat(k[4]),
          volume: parseFloat(k[5]),
        }));

        setCandles(parsedCandles);
        setCurrentPrice(parseFloat(ticker.lastPrice));
        setChange24h(parseFloat(ticker.priceChangePercent));
        setError(false);
      }
    } catch (e) {
      console.warn(`[MiniCandleChart] ${symbol} fetch error:`, e);
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [symbol, isForex, binanceSymbol]);

  useEffect(() => {
    fetchData();
    timerRef.current = setInterval(fetchData, 30000);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [fetchData]);

  // ── SVG Candle Rendering ──────────────────────────────────────────────────
  function renderCandles() {
    if (candles.length === 0) return null;
    const prices = candles.flatMap((c) => [c.high, c.low]);
    const minP = Math.min(...prices);
    const maxP = Math.max(...prices);
    const range = maxP - minP || 1;
    const candleW = Math.max(2, Math.floor(CHART_W / candles.length) - 1);
    const toY = (p: number) => CHART_H - ((p - minP) / range) * CHART_H;

    return candles.map((c, i) => {
      const x = i * (CHART_W / candles.length);
      const cx = x + candleW / 2;
      const isGreen = c.close >= c.open;
      const color = isGreen ? '#10b981' : '#f43f5e';
      const bodyTop = toY(Math.max(c.open, c.close));
      const bodyBot = toY(Math.min(c.open, c.close));
      const bodyH = Math.max(1, bodyBot - bodyTop);
      const wickTop = toY(c.high);
      const wickBot = toY(c.low);

      return (
        <g key={i}>
          {/* Wick */}
          <line
            x1={cx}
            y1={wickTop}
            x2={cx}
            y2={wickBot}
            stroke={color}
            strokeWidth={1}
            opacity={0.7}
          />
          {/* Body */}
          <rect
            x={x}
            y={bodyTop}
            width={candleW}
            height={bodyH}
            fill={color}
            opacity={0.9}
          />
        </g>
      );
    });
  }

  // ── Signal badge colors ───────────────────────────────────────────────────
  const signalColors = {
    BUY: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40',
    SELL: 'bg-rose-500/20 text-rose-400 border-rose-500/40',
    HOLD: 'bg-amber-500/20 text-amber-400 border-amber-500/40',
  };

  const isPositive = (change24h ?? 0) >= 0;

  // ── Format price ─────────────────────────────────────────────────────────
  const formatPrice = (p: number) => {
    if (p >= 1000) return p.toLocaleString('en-US', { maximumFractionDigits: 2 });
    if (p >= 1) return p.toFixed(4);
    return p.toFixed(6);
  };

  return (
    <div
      onClick={() => onSelect?.(symbol)}
      className={cn(
        'relative flex flex-col rounded-xl border cursor-pointer transition-all duration-200 overflow-hidden group',
        'bg-[#141416] hover:bg-[#1a1a1e]',
        isSelected
          ? 'border-emerald-500/60 shadow-emerald-500/10 shadow-md'
          : 'border-zinc-800 hover:border-zinc-700'
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-3 pt-2.5 pb-1">
        <div className="flex items-center gap-1.5">
          <span className="text-[11px] font-bold text-white tracking-wide">
            {displayName}
          </span>
          {signal && (
            <span
              className={cn(
                'text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded-full border',
                signalColors[signal]
              )}
            >
              {signal}
            </span>
          )}
        </div>
        {change24h !== null && (
          <span
            className={cn(
              'text-[10px] font-bold font-mono',
              isPositive ? 'text-emerald-400' : 'text-rose-400'
            )}
          >
            {isPositive ? '+' : ''}
            {change24h.toFixed(2)}%
          </span>
        )}
      </div>

      {/* SVG Chart */}
      <div className="px-2">
        {loading ? (
          <div
            className="flex items-center justify-center"
            style={{ height: CHART_H }}
          >
            <div className="w-4 h-4 border-2 border-zinc-600 border-t-emerald-500 rounded-full animate-spin" />
          </div>
        ) : error ? (
          <div
            className="flex items-center justify-center text-zinc-600 text-[10px]"
            style={{ height: CHART_H }}
          >
            No data
          </div>
        ) : (
          <svg
            width={CHART_W}
            height={CHART_H}
            className="w-full"
            viewBox={`0 0 ${CHART_W} ${CHART_H}`}
            preserveAspectRatio="none"
          >
            {renderCandles()}
          </svg>
        )}
      </div>

      {/* Price */}
      <div className="px-3 pb-2 flex items-center justify-between">
        <span className="text-[12px] font-mono font-bold text-white">
          {currentPrice !== null ? formatPrice(currentPrice) : '—'}
        </span>
        {/* Quick Trade Buttons */}
        <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            onClick={(e) => {
              e.stopPropagation();
              onQuickTrade?.(symbol, 'buy');
            }}
            className="px-2 py-0.5 text-[9px] font-bold uppercase bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/40 rounded border border-emerald-500/40 transition-colors"
          >
            B
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation();
              onQuickTrade?.(symbol, 'sell');
            }}
            className="px-2 py-0.5 text-[9px] font-bold uppercase bg-rose-500/20 text-rose-400 hover:bg-rose-500/40 rounded border border-rose-500/40 transition-colors"
          >
            S
          </button>
        </div>
      </div>

      {/* Selected indicator */}
      {isSelected && (
        <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-gradient-to-r from-emerald-500 to-teal-500" />
      )}
    </div>
  );
}
