import React, { useEffect, useRef, useState, useCallback } from 'react';
import {
  Maximize2,
  Minimize2,
  RefreshCw,
  Sliders,
  Layers,
  LayoutGrid,
  Zap,
  TrendingUp,
  Activity,
  ArrowUpRight,
  ArrowDownRight,
  Shield,
  Eye,
  BarChart2,
  Sparkles,
  Info,
  CheckCircle2,
} from 'lucide-react';
import { apiService } from '../services/apiService';
import { TradingChart } from './TradingChart';
import { cn } from '../lib/utils';

interface OpenApiChartViewProps {
  initialSymbol?: string;
  initialTimeframe?: string;
  onQuickTrade?: (symbol: string, direction: 'BUY' | 'SELL') => void;
}

const SUPPORTED_SYMBOLS = [
  { symbol: 'EURUSD', display: 'EUR/USD', category: 'Forex Major' },
  { symbol: 'GBPUSD', display: 'GBP/USD', category: 'Forex Major' },
  { symbol: 'USDJPY', display: 'USD/JPY', category: 'Forex Major' },
  { symbol: 'AUDUSD', display: 'AUD/USD', category: 'Forex Major' },
  { symbol: 'USDCAD', display: 'USD/CAD', category: 'Forex Major' },
  { symbol: 'USDCHF', display: 'USD/CHF', category: 'Forex Major' },
  { symbol: 'NZDUSD', display: 'NZD/USD', category: 'Forex Major' },
  { symbol: 'EURGBP', display: 'EUR/GBP', category: 'Forex Cross' },
  { symbol: 'EURJPY', display: 'EUR/JPY', category: 'Forex Cross' },
  { symbol: 'GBPJPY', display: 'GBP/JPY', category: 'Forex Cross' },
  { symbol: 'XAUUSD', display: 'Gold / USD', category: 'Commodities' },
  { symbol: 'BTCUSD', display: 'Bitcoin / USD', category: 'Crypto' },
  { symbol: 'ETHUSD', display: 'Ethereum / USD', category: 'Crypto' },
  { symbol: 'SOLUSD', display: 'Solana / USD', category: 'Crypto' },
];

const TIMEFRAMES = [
  { id: '1M', label: '1m' },
  { id: '5M', label: '5m' },
  { id: '15M', label: '15m' },
  { id: '30M', label: '30m' },
  { id: '1H', label: '1h' },
  { id: '4H', label: '4h' },
  { id: '1D', label: '1D' },
  { id: '1W', label: '1W' },
];

const LAYOUTS = [
  { id: 0, label: 'Single', icon: '1' },
  { id: 1, label: 'Split H', icon: '2H' },
  { id: 2, label: 'Split V', icon: '2V' },
  { id: 5, label: 'Quarters', icon: '4Q' },
];

export const OpenApiChartView: React.FC<OpenApiChartViewProps> = ({
  initialSymbol = 'EURUSD',
  initialTimeframe = '5M',
  onQuickTrade,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartInstanceRef = useRef<any>(null);
  const [activeSymbol, setActiveSymbol] = useState(initialSymbol);
  const [activeTimeframe, setActiveTimeframe] = useState(initialTimeframe);
  const [chartEngine, setChartEngine] = useState<'spotware' | 'lightweight'>('spotware');
  const [isLoading, setIsLoading] = useState(true);
  const [activeLayout, setActiveLayout] = useState(0);
  const [candleData, setCandleData] = useState<any[]>([]);
  const [spec, setSpec] = useState<any>(null);
  const [quickLots, setQuickLots] = useState(0.1);
  const [tradeStatus, setTradeStatus] = useState<string | null>(null);

  // Load symbol specs
  useEffect(() => {
    apiService
      .getCTraderSymbolSpec(activeSymbol)
      .then((data) => setSpec(data))
      .catch(() => {});
  }, [activeSymbol]);

  // Fetch candle data for lightweight fallback or backup
  const fetchCandles = useCallback(async () => {
    setIsLoading(true);
    try {
      const res: any = await apiService.getCTraderTrendbars(activeSymbol, activeTimeframe, 150);
      if (res && res.bars) {
        setCandleData(res.bars);
      }
    } catch (err) {
      console.warn('Failed to fetch trendbars', err);
    } finally {
      setIsLoading(false);
    }
  }, [activeSymbol, activeTimeframe]);

  useEffect(() => {
    fetchCandles();
  }, [fetchCandles]);

  // Initialize Spotware OpenAPI Chart Library
  useEffect(() => {
    if (chartEngine !== 'spotware') return;

    let isMounted = true;

    const initChart = () => {
      if (!containerRef.current || !(window as any).T4PChart) {
        return false;
      }

      try {
        // Clear previous children
        containerRef.current.innerHTML = '';

        const options = {
          general: {
            defaultSymbol: activeSymbol,
            defaultTitle: activeSymbol,
            defaultTimeframe: activeTimeframe,
            defaultChartType: 'candles',
            defaultScale: 1,
            defaultLayout: activeLayout,
            displayChange: true,
            displayIndicatorNames: true,
            displayVolume: true,
            saveLayout: false,
            saveIndicators: true,
            saveDrawings: true,
          },
          colors: {
            background: '#090a0f',
            frame: '#1e293b',
            frameActive: '#06b6d4',
            frameFullscreen: '#f59e0b',
            frameControlled: '#ef4444',
            grid: '#141a29',
            axisLine: '#1e293b',
            axisText: '#64748b',
            crossLine: '#06b6d4',
            candleRise: '#10b981',
            candleRiseBorder: '#059669',
            candleFall: '#f43f5e',
            candleFallBorder: '#e11d48',
            candleShadow: '#64748b',
            barRise: '#10b981',
            barFall: '#f43f5e',
            line: '#06b6d4',
            areaLine: '#06b6d4',
            areaBackground: 'rgba(6, 182, 212, 0.12)',
            title: '#f1f5f9',
          },
          toolbar: {
            disable: false,
            timeframes: {
              '1M': '1 Minute',
              '5M': '5 Minutes',
              '15M': '15 Minutes',
              '30M': '30 Minutes',
              '1H': '1 Hour',
              '4H': '4 Hours',
              '1D': '1 Day',
              '1W': '1 Week',
            },
            elements: [
              'symbol',
              'timeframe',
              'type',
              'indicators',
              'drawings',
              'multiview',
              'clear',
              'screenshot',
            ],
          },
        };

        const chart = new (window as any).T4PChart(containerRef.current, options);
        chartInstanceRef.current = chart;

        // Set available symbol catalog
        if (chart.data && typeof chart.data.setSymbols === 'function') {
          chart.data.setSymbols(SUPPORTED_SYMBOLS.map((s) => s.symbol));
        }

        // Add dynamic historical candle request handler
        chart.addEventHandler(
          'onCandlesRequest',
          async (
            sym: string,
            tf: string,
            fromTs: number,
            toTs: number,
            count: number,
            callback: (candles: any[]) => void
          ) => {
            try {
              const res: any = await apiService.getCTraderTrendbars(sym || activeSymbol, tf || activeTimeframe, count || 120);
              if (res && res.bars && typeof callback === 'function') {
                const formatted = res.bars.map((b: any) => ({
                  symbol: sym || activeSymbol,
                  timeframe: tf || activeTimeframe,
                  timestamp: Math.floor(b.timestamp / 1000),
                  open: b.open,
                  high: b.high,
                  low: b.low,
                  close: b.close,
                  volume: b.volume,
                }));
                callback(formatted);
              }
            } catch (e) {
              console.warn('onCandlesRequest error', e);
            }
          }
        );

        // Preload initial candles
        apiService
          .getCTraderTrendbars(activeSymbol, activeTimeframe, 120)
          .then((res: any) => {
            if (res && res.bars && chart.data && typeof chart.data.setCandles === 'function') {
              const formatted = res.bars.map((b: any) => ({
                symbol: activeSymbol,
                timeframe: activeTimeframe,
                timestamp: Math.floor(b.timestamp / 1000),
                open: b.open,
                high: b.high,
                low: b.low,
                close: b.close,
                volume: b.volume,
              }));
              chart.data.setCandles(formatted);
            }
          })
          .catch(() => {});

        return true;
      } catch (err) {
        console.error('Error initializing Spotware Charting Library:', err);
        return false;
      }
    };

    // Check if script tag already exists
    if (!(window as any).T4PChart) {
      const script = document.createElement('script');
      script.src = '/libs/chart-api.min.js';
      script.async = true;
      script.onload = () => {
        if (isMounted) initChart();
      };
      document.body.appendChild(script);
    } else {
      initChart();
    }

    return () => {
      isMounted = false;
      if (chartInstanceRef.current && typeof chartInstanceRef.current.drop === 'function') {
        try {
          chartInstanceRef.current.drop();
        } catch (e) {}
      }
    };
  }, [chartEngine, activeSymbol, activeTimeframe, activeLayout]);

  const handleSymbolChange = (sym: string) => {
    setActiveSymbol(sym);
    if (chartInstanceRef.current && typeof chartInstanceRef.current.setSymbol === 'function') {
      chartInstanceRef.current.setSymbol(sym);
      chartInstanceRef.current.setTitle(sym);
    }
  };

  const handleTimeframeChange = (tf: string) => {
    setActiveTimeframe(tf);
    if (chartInstanceRef.current && typeof chartInstanceRef.current.setTimeframe === 'function') {
      chartInstanceRef.current.setTimeframe(tf);
    }
  };

  const handleLayoutChange = (layoutId: number) => {
    setActiveLayout(layoutId);
    if (chartInstanceRef.current && typeof chartInstanceRef.current.setLayout === 'function') {
      chartInstanceRef.current.setLayout(layoutId);
    }
  };

  const executeOrder = async (direction: 'BUY' | 'SELL') => {
    try {
      setTradeStatus(`Routing ${direction} ${quickLots} lots on ${activeSymbol}...`);
      const res: any = await apiService.placeSmartOrder({
        symbol: activeSymbol,
        direction: direction,
        quantity: quickLots,
        order_type: 'MARKET',
        broker_override: 'ctrader',
      });
      if (res && res.success) {
        setTradeStatus(`Order executed! ID: ${res.order_id || 'Filled'}`);
        setTimeout(() => setTradeStatus(null), 4000);
      } else {
        setTradeStatus(`Notice: ${res.message || res.status || 'Dispatched'}`);
        setTimeout(() => setTradeStatus(null), 4000);
      }
      if (onQuickTrade) {
        onQuickTrade(activeSymbol, direction);
      }
    } catch (err: any) {
      setTradeStatus(`Error: ${err.message || 'Execution failed'}`);
      setTimeout(() => setTradeStatus(null), 4000);
    }
  };

  return (
    <div className="flex-1 flex flex-col h-full bg-[#08090d] text-slate-100 overflow-hidden select-none">
      {/* ── Top Bar: Symbol Selector, Timeframe Pills, Multi-Layout & Engine Switcher ── */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-[#0d0f17] border-b border-slate-800/80 shrink-0 gap-3 flex-wrap">
        {/* Symbol Dropdown */}
        <div className="flex items-center gap-3">
          <div className="relative">
            <select
              value={activeSymbol}
              onChange={(e) => handleSymbolChange(e.target.value)}
              className="bg-slate-900/90 text-cyan-400 font-mono font-bold text-sm px-3 py-1.5 rounded-lg border border-slate-700/80 focus:outline-none focus:border-cyan-500 hover:bg-slate-800 transition-colors cursor-pointer"
            >
              {SUPPORTED_SYMBOLS.map((s) => (
                <option key={s.symbol} value={s.symbol} className="bg-slate-900 text-slate-200">
                  {s.display} ({s.category})
                </option>
              ))}
            </select>
          </div>

          {spec && (
            <div className="hidden md:flex items-center gap-2 text-[11px] font-mono text-slate-400 bg-slate-900/60 px-2.5 py-1 rounded-md border border-slate-800">
              <span>Pip: <strong className="text-cyan-400">{spec.pip_size}</strong></span>
              <span className="text-slate-600">|</span>
              <span>Digits: <strong className="text-slate-200">{spec.digits}</strong></span>
              <span className="text-slate-600">|</span>
              <span>1 Lot = <strong className="text-emerald-400">100k</strong></span>
            </div>
          )}
        </div>

        {/* Timeframe Selector Pills */}
        <div className="flex items-center bg-slate-900/80 p-0.5 rounded-lg border border-slate-800">
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf.id}
              onClick={() => handleTimeframeChange(tf.id)}
              className={cn(
                'px-2.5 py-1 text-xs font-mono font-medium rounded-md transition-all',
                activeTimeframe === tf.id
                  ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 shadow-sm shadow-cyan-500/10'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
              )}
            >
              {tf.label}
            </button>
          ))}
        </div>

        {/* Multi-view Layout Controls & Engine Switcher */}
        <div className="flex items-center gap-2">
          {/* Multi-View layout selector (Spotware Canvas only) */}
          {chartEngine === 'spotware' && (
            <div className="hidden lg:flex items-center gap-1 bg-slate-900/80 p-0.5 rounded-lg border border-slate-800">
              {LAYOUTS.map((l) => (
                <button
                  key={l.id}
                  onClick={() => handleLayoutChange(l.id)}
                  title={`Layout: ${l.label}`}
                  className={cn(
                    'px-2 py-0.5 text-[10px] font-mono font-bold rounded transition-all',
                    activeLayout === l.id
                      ? 'bg-amber-500/20 text-amber-300 border border-amber-500/40'
                      : 'text-slate-500 hover:text-slate-300'
                  )}
                >
                  {l.icon}
                </button>
              ))}
            </div>
          )}

          {/* Engine Switcher */}
          <div className="flex items-center bg-slate-900/90 p-0.5 rounded-lg border border-slate-800 text-[11px] font-medium">
            <button
              onClick={() => setChartEngine('spotware')}
              className={cn(
                'px-2.5 py-1 rounded transition-all flex items-center gap-1.5',
                chartEngine === 'spotware'
                  ? 'bg-cyan-500 text-slate-950 font-bold shadow'
                  : 'text-slate-400 hover:text-white'
              )}
            >
              <Sparkles size={12} />
              OpenAPI Canvas
            </button>
            <button
              onClick={() => setChartEngine('lightweight')}
              className={cn(
                'px-2.5 py-1 rounded transition-all flex items-center gap-1.5',
                chartEngine === 'lightweight'
                  ? 'bg-cyan-500 text-slate-950 font-bold shadow'
                  : 'text-slate-400 hover:text-white'
              )}
            >
              <BarChart2 size={12} />
              Lightweight
            </button>
          </div>

          <button
            onClick={fetchCandles}
            className="p-1.5 bg-slate-900 text-slate-400 hover:text-cyan-400 hover:bg-slate-800 rounded-lg border border-slate-800 transition-colors"
            title="Refresh Candles"
          >
            <RefreshCw size={14} className={isLoading ? 'animate-spin text-cyan-400' : ''} />
          </button>
        </div>
      </div>

      {/* ── Main Chart Workspace ── */}
      <div className="flex-1 relative overflow-hidden bg-[#07080c]">
        {chartEngine === 'spotware' ? (
          <div
            ref={containerRef}
            id="spotware-chart-root"
            className="w-full h-full absolute inset-0"
            style={{ width: '100%', height: '100%', minHeight: '400px' }}
          />
        ) : (
          <div className="w-full h-full p-4 flex flex-col">
            <div className="flex-1 bg-slate-950/60 rounded-xl border border-slate-800/80 p-2 overflow-hidden">
              <TradingChart data={candleData} />
            </div>
          </div>
        )}

        {/* Quick Trade Floating HUD */}
        <div className="absolute bottom-4 right-4 z-20 bg-slate-900/90 backdrop-blur-md p-3 rounded-2xl border border-slate-800 shadow-2xl flex flex-col gap-2.5 w-64">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-mono font-bold text-slate-400 flex items-center gap-1">
              <Zap size={13} className="text-amber-400" />
              QUICK ORDER: <span className="text-white">{activeSymbol}</span>
            </span>
            <span className="text-[10px] font-mono text-cyan-400 bg-cyan-950/60 px-1.5 py-0.5 rounded border border-cyan-800/50">
              cTrader
            </span>
          </div>

          <div className="flex items-center gap-2">
            <label className="text-[10px] text-slate-400 font-mono">Lots:</label>
            <input
              type="number"
              step="0.01"
              min="0.01"
              max="50"
              value={quickLots}
              onChange={(e) => setQuickLots(Math.max(0.01, parseFloat(e.target.value) || 0.01))}
              className="flex-1 bg-slate-950 text-white font-mono text-xs px-2 py-1 rounded border border-slate-800 focus:outline-none focus:border-cyan-500 text-right"
            />
          </div>

          <div className="grid grid-cols-2 gap-2">
            <button
              onClick={() => executeOrder('BUY')}
              className="flex items-center justify-center gap-1 py-2 px-3 rounded-xl bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-400 border border-emerald-500/40 text-xs font-bold font-mono transition-all active:scale-95 shadow-sm shadow-emerald-500/10"
            >
              <ArrowUpRight size={14} /> BUY
            </button>
            <button
              onClick={() => executeOrder('SELL')}
              className="flex items-center justify-center gap-1 py-2 px-3 rounded-xl bg-rose-600/20 hover:bg-rose-600/30 text-rose-400 border border-rose-500/40 text-xs font-bold font-mono transition-all active:scale-95 shadow-sm shadow-rose-500/10"
            >
              <ArrowDownRight size={14} /> SELL
            </button>
          </div>

          {tradeStatus && (
            <div className="text-[10px] font-mono text-center text-cyan-300 bg-slate-950/80 py-1 px-2 rounded border border-slate-800 animate-pulse">
              {tradeStatus}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
export default OpenApiChartView;
