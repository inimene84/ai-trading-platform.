/** Helpers for Spotware T4PChart candle ingestion. */

export const TF_ALIASES: Record<string, string[]> = {
  '1M': ['1M', 'M1'],
  M1: ['1M', 'M1'],
  '5M': ['5M', 'M5'],
  M5: ['5M', 'M5'],
  '15M': ['15M', 'M15'],
  M15: ['15M', 'M15'],
  '30M': ['30M', 'M30'],
  M30: ['30M', 'M30'],
  '1H': ['1H', 'H1'],
  H1: ['1H', 'H1'],
  '4H': ['4H', 'H4'],
  H4: ['4H', 'H4'],
  '1D': ['1D', 'D1'],
  D1: ['1D', 'D1'],
  '1W': ['1W', 'W1'],
  W1: ['1W', 'W1'],
};

export const CANDLE_REQUEST_EVENTS = [
  'onCandlesRequest',
  'onHistoryRequest',
  'onDataRequest',
  'candles',
];

export function timeframeAliases(timeframe: string): string[] {
  const mapped = TF_ALIASES[timeframe];
  if (mapped) return [...mapped];
  return [timeframe];
}

/** T4PChart stores unix seconds (`new Date(1000 * timestamp)`). */
export function toUnixSeconds(value: unknown): number {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return 0;
  return n > 1e12 ? Math.floor(n / 1000) : Math.floor(n);
}

export interface SpotwareCandle {
  symbol: string;
  timeframe: string;
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export function formatSpotwareCandles(
  bars: any[],
  symbol: string,
  timeframe: string
): SpotwareCandle[] {
  return (bars || [])
    .map((b: any) => ({
      symbol,
      timeframe,
      timestamp: toUnixSeconds(b.time ?? b.timestamp),
      open: Number(b.open),
      high: Number(b.high),
      low: Number(b.low),
      close: Number(b.close),
      volume: Number(b.volume || 0),
    }))
    .filter((b) => b.timestamp > 0 && b.close > 0)
    .sort((a, b) => a.timestamp - b.timestamp);
}

export function toLightweightBars(bars: any[]) {
  return (bars || [])
    .map((b: any) => ({
      time: toUnixSeconds(b.time ?? b.timestamp),
      open: Number(b.open),
      high: Number(b.high),
      low: Number(b.low),
      close: Number(b.close),
      volume: Number(b.volume || 0),
    }))
    .filter((b) => b.time > 0 && b.close > 0)
    .sort((a, b) => a.time - b.time);
}

function existingCount(chart: any, symbol: string, timeframe: string): number {
  if (typeof chart.data?.count !== 'function') return 0;
  return Number(chart.data.count(symbol, timeframe) || 0);
}

function markBucketComplete(bucket: any) {
  if (bucket && typeof bucket === 'object') {
    bucket.complete = true;
    bucket.pending = false;
  }
}

/**
 * T4PChart's panel keeps LOADING up until stg.complete(symbol, tf) is truthy
 * (otherwise it waits for 1000 bars). The library never sets that flag itself.
 */
export function armSpotwareLoader(chart: any) {
  if (!chart?.data || chart.data.__qtLoaderArmed) return;
  chart.data.__qtLoaderArmed = true;
  const origComplete = chart.data.complete?.bind(chart.data);
  chart.data.complete = function complete(symbol?: string, timeframe?: string) {
    if (typeof origComplete === 'function') {
      try {
        origComplete(symbol, timeframe);
      } catch {
        /* getter only */
      }
    }
    return true;
  };
  dismissSpotwareLoader(chart);
}

export function dismissSpotwareLoader(chart: any) {
  if (!chart) return;
  if (typeof chart.hideLoader === 'function') {
    try {
      chart.hideLoader();
    } catch {
      /* ignore */
    }
  }
  if (typeof chart.fireEvent === 'function') {
    try {
      chart.fireEvent('onChartReady');
    } catch {
      /* ignore */
    }
  }
  if (typeof document !== 'undefined') {
    document.querySelectorAll('.ui-loader').forEach((el) => {
      const wrap = el.parentElement;
      if (wrap instanceof HTMLElement) wrap.style.display = 'none';
      if (el instanceof HTMLElement) el.style.display = 'none';
    });
  }
}

/**
 * T4PChart.setCandles throws unless `data.empty(symbol, tf)` created the bucket.
 * Re-calling empty() on retries replaces the bucket and drops complete=true.
 */
export function pushCandlesToChart(
  chart: any,
  bars: any[],
  symbol: string,
  timeframe: string
): boolean {
  if (!chart?.data || !bars?.length) return false;
  const tfs = timeframeAliases(timeframe);
  let pushed = false;
  try {
    for (const tf of tfs) {
      let bucket: any;
      const already = existingCount(chart, symbol, tf);
      if (already === 0 && typeof chart.data.empty === 'function') {
        bucket = chart.data.empty(symbol, tf);
        markBucketComplete(bucket);
      }
      const formatted = formatSpotwareCandles(bars, symbol, tf);
      if (formatted.length && typeof chart.data.setCandles === 'function') {
        chart.data.setCandles(formatted);
        pushed = true;
      }
      markBucketComplete(bucket);
    }
    if (pushed) {
      armSpotwareLoader(chart);
      dismissSpotwareLoader(chart);
    }
    return pushed;
  } catch (err) {
    console.warn('Failed to push candles into T4PChart', err);
    return false;
  }
}

export function chartCandleCount(chart: any, symbol: string, timeframe: string): number {
  if (!chart?.data || typeof chart.data.count !== 'function') return 0;
  for (const tf of timeframeAliases(timeframe)) {
    const n = Number(chart.data.count(symbol, tf) || 0);
    if (n > 0) return n;
  }
  return 0;
}
