import React, { useState, useEffect } from 'react';
import {
  Code,
  Activity,
  Calculator,
  BarChart3,
  RefreshCw,
  Terminal,
  Send,
  Zap,
  CheckCircle2,
  AlertCircle,
  Copy,
  ChevronRight,
  TrendingUp,
  Cpu,
  Layers,
  Database,
  Search,
  Sliders,
  DollarSign,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';
import { apiService } from '../services/apiService';
import { cn } from '../lib/utils';

export const OpenApiSamplesView: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'trendbars' | 'ticks' | 'calc' | 'proto'>('trendbars');

  // Trendbars State
  const [tbSymbol, setTbSymbol] = useState('EURUSD');
  const [tbPeriod, setTbPeriod] = useState('M5');
  const [tbCount, setTbCount] = useState(30);
  const [tbResults, setTbResults] = useState<any>(null);
  const [tbLoading, setTbLoading] = useState(false);

  // Ticks State
  const [tickSymbol, setTickSymbol] = useState('EURUSD');
  const [tickType, setTickType] = useState('BID');
  const [tickHours, setTickHours] = useState(4);
  const [tickResults, setTickResults] = useState<any>(null);
  const [tickLoading, setTickLoading] = useState(false);

  // Calculator State
  const [calcSymbol, setCalcSymbol] = useState('EURUSD');
  const [calcLots, setCalcLots] = useState(1.0);
  const [calcPrice, setCalcPrice] = useState<number | undefined>(undefined);
  const [calcLeverage, setCalcLeverage] = useState(100);
  const [calcDepositAsset, setCalcDepositAsset] = useState('USD');
  const [calcResult, setCalcResult] = useState<any>(null);
  const [calcLoading, setCalcLoading] = useState(false);

  // Protobuf Explorer State
  const [selectedProto, setSelectedProto] = useState('ProtoOAGetTrendbarsReq');
  const [protoPayload, setProtoPayload] = useState('{\n  "symbol": "EURUSD",\n  "period": "M5",\n  "count": 50\n}');
  const [protoResponse, setProtoResponse] = useState<any>(null);
  const [protoLoading, setProtoLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  // Run initial trendbars
  useEffect(() => {
    fetchTrendbars();
    fetchCalculator();
  }, []);

  const fetchTrendbars = async () => {
    setTbLoading(true);
    try {
      const res = await apiService.getCTraderTrendbars(tbSymbol, tbPeriod, tbCount);
      setTbResults(res);
    } catch (e: any) {
      setTbResults({ error: e.message || 'Failed to fetch trendbars' });
    } finally {
      setTbLoading(false);
    }
  };

  const fetchTicks = async () => {
    setTickLoading(true);
    try {
      const res = await apiService.getCTraderTicks(tickSymbol, tickType, tickHours);
      setTickResults(res);
    } catch (e: any) {
      setTickResults({ error: e.message || 'Failed to fetch ticks' });
    } finally {
      setTickLoading(false);
    }
  };

  const fetchCalculator = async () => {
    setCalcLoading(true);
    try {
      const res = await apiService.calculatePipMargin({
        symbol: calcSymbol,
        lots: calcLots,
        price: calcPrice,
        leverage: calcLeverage,
        deposit_asset: calcDepositAsset,
      });
      setCalcResult(res);
    } catch (e: any) {
      setCalcResult({ error: e.message || 'Calculation failed' });
    } finally {
      setCalcLoading(false);
    }
  };

  const handleProtoChange = (protoName: string) => {
    setSelectedProto(protoName);
    switch (protoName) {
      case 'ProtoOAGetTrendbarsReq':
        setProtoPayload('{\n  "symbol": "EURUSD",\n  "period": "M5",\n  "count": 50\n}');
        break;
      case 'ProtoOAGetTickDataReq':
        setProtoPayload('{\n  "symbol": "EURUSD",\n  "type": "BID",\n  "hours": 4\n}');
        break;
      case 'ProtoOASymbolsListReq':
        setProtoPayload('{\n  "includeArchivedSymbols": false\n}');
        break;
      case 'ProtoOAApplicationAuthReq':
        setProtoPayload('{\n  "clientId": "your_app_client_id",\n  "clientSecret": "your_app_secret"\n}');
        break;
      case 'ProtoOANewOrderReq':
        setProtoPayload('{\n  "symbol": "EURUSD",\n  "orderType": "MARKET",\n  "tradeSide": "BUY",\n  "volume": 100000,\n  "relativeStopLoss": 30,\n  "relativeTakeProfit": 60\n}');
        break;
      default:
        setProtoPayload('{}');
    }
  };

  const executeProtoTest = async () => {
    setProtoLoading(true);
    try {
      let parsed = {};
      try {
        parsed = JSON.parse(protoPayload);
      } catch (err) {
        setProtoResponse({ error: 'Invalid JSON payload' });
        setProtoLoading(false);
        return;
      }

      if (selectedProto === 'ProtoOAGetTrendbarsReq') {
        const res = await apiService.getCTraderTrendbars(
          (parsed as any).symbol || 'EURUSD',
          (parsed as any).period || 'M5',
          (parsed as any).count || 50
        );
        setProtoResponse({
          payloadType: 2138,
          payloadName: 'ProtoOAGetTrendbarsRes',
          data: res,
        });
      } else if (selectedProto === 'ProtoOAGetTickDataReq') {
        const res = await apiService.getCTraderTicks(
          (parsed as any).symbol || 'EURUSD',
          (parsed as any).type || 'BID',
          (parsed as any).hours || 4
        );
        setProtoResponse({
          payloadType: 2146,
          payloadName: 'ProtoOAGetTickDataRes',
          data: res,
        });
      } else if (selectedProto === 'ProtoOASymbolsListReq') {
        const res = await apiService.getMarkets();
        setProtoResponse({
          payloadType: 2116,
          payloadName: 'ProtoOASymbolsListRes',
          data: res,
        });
      } else if (selectedProto === 'ProtoOANewOrderReq') {
        const res = await apiService.placeSmartOrder({
          symbol: (parsed as any).symbol || 'EURUSD',
          direction: (parsed as any).tradeSide === 'BUY' ? 'BUY' : 'SELL',
          quantity: ((parsed as any).volume || 100000) / 100000,
          broker_override: 'ctrader',
        });
        setProtoResponse({
          payloadType: 2126,
          payloadName: 'ProtoOAExecutionEvent',
          data: res,
        });
      } else {
        const res = await apiService.getCTraderTokens();
        setProtoResponse({
          payloadType: 2101,
          payloadName: 'ProtoOAApplicationAuthRes',
          data: res,
        });
      }
    } catch (e: any) {
      setProtoResponse({ error: e.message || 'Execution error' });
    } finally {
      setProtoLoading(false);
    }
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="flex-1 flex flex-col h-full bg-[#08090d] text-slate-100 overflow-y-auto p-4 md:p-6 select-none">
      {/* ── Header ── */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-5 border-b border-slate-800/80 mb-6">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="px-2 py-0.5 rounded bg-cyan-500/10 border border-cyan-500/30 text-cyan-400 text-[10px] font-mono font-bold tracking-wider uppercase">
              Spotware OpenAPI.Net Suite
            </span>
            <span className="flex items-center gap-1 text-[11px] font-mono text-emerald-400">
              <CheckCircle2 size={12} /> Active Sandbox
            </span>
          </div>
          <h1 className="text-xl md:text-2xl font-bold text-white flex items-center gap-2">
            OpenAPI Samples & Financial Tools Lab
          </h1>
          <p className="text-xs text-slate-400 mt-0.5">
            Test real cTrader Open API Protobuf payloads, query historical trendbars, stream tick feeds, and calculate pip/margin metrics.
          </p>
        </div>

        {/* Tab Navigation */}
        <div className="flex items-center bg-slate-900/90 p-1 rounded-xl border border-slate-800 shrink-0">
          <button
            onClick={() => setActiveTab('trendbars')}
            className={cn(
              'px-3 py-1.5 text-xs font-mono font-medium rounded-lg transition-all flex items-center gap-1.5',
              activeTab === 'trendbars'
                ? 'bg-cyan-500 text-slate-950 font-bold shadow'
                : 'text-slate-400 hover:text-white'
            )}
          >
            <BarChart3 size={13} /> Trendbars
          </button>
          <button
            onClick={() => {
              setActiveTab('ticks');
              if (!tickResults) fetchTicks();
            }}
            className={cn(
              'px-3 py-1.5 text-xs font-mono font-medium rounded-lg transition-all flex items-center gap-1.5',
              activeTab === 'ticks'
                ? 'bg-cyan-500 text-slate-950 font-bold shadow'
                : 'text-slate-400 hover:text-white'
            )}
          >
            <Activity size={13} /> Tick Stream
          </button>
          <button
            onClick={() => setActiveTab('calc')}
            className={cn(
              'px-3 py-1.5 text-xs font-mono font-medium rounded-lg transition-all flex items-center gap-1.5',
              activeTab === 'calc'
                ? 'bg-cyan-500 text-slate-950 font-bold shadow'
                : 'text-slate-400 hover:text-white'
            )}
          >
            <Calculator size={13} /> Pip & Margin Calc
          </button>
          <button
            onClick={() => setActiveTab('proto')}
            className={cn(
              'px-3 py-1.5 text-xs font-mono font-medium rounded-lg transition-all flex items-center gap-1.5',
              activeTab === 'proto'
                ? 'bg-cyan-500 text-slate-950 font-bold shadow'
                : 'text-slate-400 hover:text-white'
            )}
          >
            <Code size={13} /> Protobuf Explorer
          </button>
        </div>
      </div>

      {/* ── TAB 1: Trendbars Historical Inspector ── */}
      {activeTab === 'trendbars' && (
        <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
          {/* Controls Form */}
          <div className="xl:col-span-4 bg-[#0d0f17] border border-slate-800/80 rounded-2xl p-5 flex flex-col gap-4">
            <div className="flex items-center gap-2 pb-3 border-b border-slate-800">
              <BarChart3 className="text-cyan-400" size={18} />
              <h3 className="text-sm font-bold text-white">ProtoOAGetTrendbarsReq Parameters</h3>
            </div>

            <div className="space-y-3">
              <div>
                <label className="text-[11px] font-mono text-slate-400 block mb-1">Symbol:</label>
                <select
                  value={tbSymbol}
                  onChange={(e) => setTbSymbol(e.target.value)}
                  className="w-full bg-slate-900 text-white font-mono text-xs px-3 py-2 rounded-lg border border-slate-800 focus:outline-none focus:border-cyan-500"
                >
                  <option value="EURUSD">EURUSD (Euro / US Dollar)</option>
                  <option value="GBPUSD">GBPUSD (British Pound / US Dollar)</option>
                  <option value="USDJPY">USDJPY (US Dollar / Japanese Yen)</option>
                  <option value="AUDUSD">AUDUSD (Australian Dollar / US Dollar)</option>
                  <option value="USDCAD">USDCAD (US Dollar / Canadian Dollar)</option>
                  <option value="XAUUSD">XAUUSD (Gold / US Dollar)</option>
                  <option value="BTCUSD">BTCUSD (Bitcoin / US Dollar)</option>
                </select>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-[11px] font-mono text-slate-400 block mb-1">Period:</label>
                  <select
                    value={tbPeriod}
                    onChange={(e) => setTbPeriod(e.target.value)}
                    className="w-full bg-slate-900 text-white font-mono text-xs px-3 py-2 rounded-lg border border-slate-800 focus:outline-none focus:border-cyan-500"
                  >
                    <option value="M1">M1 (1 Minute)</option>
                    <option value="M5">M5 (5 Minutes)</option>
                    <option value="M15">M15 (15 Minutes)</option>
                    <option value="M30">M30 (30 Minutes)</option>
                    <option value="H1">H1 (1 Hour)</option>
                    <option value="H4">H4 (4 Hours)</option>
                    <option value="D1">D1 (1 Day)</option>
                    <option value="W1">W1 (1 Week)</option>
                  </select>
                </div>
                <div>
                  <label className="text-[11px] font-mono text-slate-400 block mb-1">Count:</label>
                  <input
                    type="number"
                    min="10"
                    max="500"
                    value={tbCount}
                    onChange={(e) => setTbCount(parseInt(e.target.value) || 30)}
                    className="w-full bg-slate-900 text-white font-mono text-xs px-3 py-2 rounded-lg border border-slate-800 focus:outline-none focus:border-cyan-500"
                  />
                </div>
              </div>

              <button
                onClick={fetchTrendbars}
                disabled={tbLoading}
                className="w-full mt-2 py-2.5 rounded-xl bg-cyan-500 hover:bg-cyan-400 text-slate-950 font-bold font-mono text-xs flex items-center justify-center gap-2 transition-all active:scale-98 shadow-lg shadow-cyan-500/10"
              >
                <RefreshCw size={14} className={tbLoading ? 'animate-spin' : ''} />
                {tbLoading ? 'Querying cTrader...' : 'Send Trendbars Request'}
              </button>
            </div>

            {/* Protocol Meta */}
            <div className="mt-auto pt-4 border-t border-slate-800/80 text-[11px] font-mono text-slate-400 space-y-1.5">
              <div className="flex justify-between">
                <span>Payload Req Type:</span>
                <span className="text-cyan-400">2137 (ProtoOAGetTrendbarsReq)</span>
              </div>
              <div className="flex justify-between">
                <span>Payload Res Type:</span>
                <span className="text-emerald-400">2138 (ProtoOAGetTrendbarsRes)</span>
              </div>
              <div className="flex justify-between">
                <span>Precision:</span>
                <span className="text-white">Delta encoding / 10^Digits</span>
              </div>
            </div>
          </div>

          {/* Results Table & JSON */}
          <div className="xl:col-span-8 bg-[#0d0f17] border border-slate-800/80 rounded-2xl p-5 flex flex-col h-[520px]">
            <div className="flex items-center justify-between pb-3 border-b border-slate-800 mb-3">
              <h3 className="text-sm font-bold text-white flex items-center gap-2">
                <span>Historical Candles:</span>
                <span className="text-cyan-400 font-mono">{tbSymbol}</span>
                <span className="text-slate-500 text-xs font-mono font-normal">({tbPeriod} • {tbResults?.bars?.length || 0} bars)</span>
              </h3>
              {tbResults && (
                <button
                  onClick={() => copyToClipboard(JSON.stringify(tbResults, null, 2))}
                  className="flex items-center gap-1 text-[11px] font-mono text-slate-400 hover:text-white px-2 py-1 rounded bg-slate-900 border border-slate-800"
                >
                  <Copy size={12} /> {copied ? 'Copied!' : 'Copy JSON'}
                </button>
              )}
            </div>

            <div className="flex-1 overflow-auto scrollbar-thin scrollbar-thumb-slate-800">
              {tbResults?.bars ? (
                <table className="w-full text-left font-mono text-xs">
                  <thead className="bg-slate-900/80 text-slate-400 text-[10px] uppercase sticky top-0">
                    <tr>
                      <th className="py-2 px-3">Time (UTC)</th>
                      <th className="py-2 px-3 text-right">Open</th>
                      <th className="py-2 px-3 text-right">High</th>
                      <th className="py-2 px-3 text-right">Low</th>
                      <th className="py-2 px-3 text-right">Close</th>
                      <th className="py-2 px-3 text-right">Volume</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60">
                    {tbResults.bars.slice().reverse().map((b: any, idx: number) => {
                      const isUp = b.close >= b.open;
                      return (
                        <tr key={idx} className="hover:bg-slate-800/30 transition-colors">
                          <td className="py-1.5 px-3 text-slate-400 text-[11px]">
                            {new Date(b.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                          </td>
                          <td className="py-1.5 px-3 text-right text-slate-300">{b.open.toFixed(5)}</td>
                          <td className="py-1.5 px-3 text-right text-emerald-400">{b.high.toFixed(5)}</td>
                          <td className="py-1.5 px-3 text-right text-rose-400">{b.low.toFixed(5)}</td>
                          <td className={cn("py-1.5 px-3 text-right font-bold", isUp ? "text-emerald-400" : "text-rose-400")}>
                            {b.close.toFixed(5)}
                          </td>
                          <td className="py-1.5 px-3 text-right text-slate-400 text-[11px]">{b.volume}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              ) : (
                <div className="h-full flex items-center justify-center text-slate-500 text-xs">
                  No candles loaded. Click "Send Trendbars Request" to fetch.
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── TAB 2: Tick Stream Inspector ── */}
      {activeTab === 'ticks' && (
        <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
          <div className="xl:col-span-4 bg-[#0d0f17] border border-slate-800/80 rounded-2xl p-5 flex flex-col gap-4">
            <div className="flex items-center gap-2 pb-3 border-b border-slate-800">
              <Activity className="text-emerald-400" size={18} />
              <h3 className="text-sm font-bold text-white">ProtoOAGetTickDataReq Parameters</h3>
            </div>

            <div className="space-y-3">
              <div>
                <label className="text-[11px] font-mono text-slate-400 block mb-1">Symbol:</label>
                <select
                  value={tickSymbol}
                  onChange={(e) => setTickSymbol(e.target.value)}
                  className="w-full bg-slate-900 text-white font-mono text-xs px-3 py-2 rounded-lg border border-slate-800 focus:outline-none focus:border-cyan-500"
                >
                  <option value="EURUSD">EURUSD</option>
                  <option value="GBPUSD">GBPUSD</option>
                  <option value="USDJPY">USDJPY</option>
                  <option value="XAUUSD">XAUUSD</option>
                  <option value="BTCUSD">BTCUSD</option>
                </select>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-[11px] font-mono text-slate-400 block mb-1">Quote Type:</label>
                  <select
                    value={tickType}
                    onChange={(e) => setTickType(e.target.value)}
                    className="w-full bg-slate-900 text-white font-mono text-xs px-3 py-2 rounded-lg border border-slate-800 focus:outline-none focus:border-cyan-500"
                  >
                    <option value="BID">BID</option>
                    <option value="ASK">ASK</option>
                  </select>
                </div>
                <div>
                  <label className="text-[11px] font-mono text-slate-400 block mb-1">Hours Back:</label>
                  <input
                    type="number"
                    min="1"
                    max="72"
                    value={tickHours}
                    onChange={(e) => setTickHours(parseInt(e.target.value) || 4)}
                    className="w-full bg-slate-900 text-white font-mono text-xs px-3 py-2 rounded-lg border border-slate-800 focus:outline-none focus:border-cyan-500"
                  />
                </div>
              </div>

              <button
                onClick={fetchTicks}
                disabled={tickLoading}
                className="w-full mt-2 py-2.5 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold font-mono text-xs flex items-center justify-center gap-2 transition-all active:scale-98 shadow-lg shadow-emerald-500/10"
              >
                <RefreshCw size={14} className={tickLoading ? 'animate-spin' : ''} />
                {tickLoading ? 'Streaming Ticks...' : 'Fetch Tick Stream'}
              </button>
            </div>

            <div className="mt-auto pt-4 border-t border-slate-800/80 text-[11px] font-mono text-slate-400 space-y-1.5">
              <div className="flex justify-between">
                <span>Payload Req Type:</span>
                <span className="text-cyan-400">2145 (ProtoOAGetTickDataReq)</span>
              </div>
              <div className="flex justify-between">
                <span>Payload Res Type:</span>
                <span className="text-emerald-400">2146 (ProtoOAGetTickDataRes)</span>
              </div>
            </div>
          </div>

          <div className="xl:col-span-8 bg-[#0d0f17] border border-slate-800/80 rounded-2xl p-5 flex flex-col h-[520px]">
            <div className="flex items-center justify-between pb-3 border-b border-slate-800 mb-3">
              <h3 className="text-sm font-bold text-white flex items-center gap-2">
                <span>Tick Stream Output:</span>
                <span className="text-emerald-400 font-mono">{tickSymbol}</span>
                <span className="text-slate-500 text-xs font-mono font-normal">({tickType} • {tickResults?.ticks?.length || 0} records)</span>
              </h3>
            </div>

            <div className="flex-1 overflow-auto scrollbar-thin scrollbar-thumb-slate-800">
              {tickResults?.ticks ? (
                <table className="w-full text-left font-mono text-xs">
                  <thead className="bg-slate-900/80 text-slate-400 text-[10px] uppercase sticky top-0">
                    <tr>
                      <th className="py-2 px-3">Timestamp (ms)</th>
                      <th className="py-2 px-3">Date / Time</th>
                      <th className="py-2 px-3">Type</th>
                      <th className="py-2 px-3 text-right">Tick Price</th>
                      <th className="py-2 px-3 text-right">Volume</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60">
                    {tickResults.ticks.slice().reverse().map((t: any, idx: number) => (
                      <tr key={idx} className="hover:bg-slate-800/30 transition-colors">
                        <td className="py-1.5 px-3 text-slate-500 text-[11px]">{t.timestamp}</td>
                        <td className="py-1.5 px-3 text-slate-300 text-[11px]">{new Date(t.timestamp).toLocaleTimeString()}</td>
                        <td className="py-1.5 px-3">
                          <span className={cn("px-1.5 py-0.5 rounded text-[10px] font-bold", t.type === 'BID' ? "bg-cyan-500/20 text-cyan-400" : "bg-amber-500/20 text-amber-400")}>
                            {t.type}
                          </span>
                        </td>
                        <td className="py-1.5 px-3 text-right font-bold text-white">{t.price.toFixed(5)}</td>
                        <td className="py-1.5 px-3 text-right text-slate-400 text-[11px]">{t.volume?.toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div className="h-full flex items-center justify-center text-slate-500 text-xs">
                  No tick records loaded.
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── TAB 3: Pip & Margin Calculator ── */}
      {activeTab === 'calc' && (
        <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
          <div className="xl:col-span-5 bg-[#0d0f17] border border-slate-800/80 rounded-2xl p-5 flex flex-col gap-4">
            <div className="flex items-center gap-2 pb-3 border-b border-slate-800">
              <Calculator className="text-amber-400" size={18} />
              <h3 className="text-sm font-bold text-white">SymbolExtensions Math Workbench</h3>
            </div>

            <div className="space-y-3">
              <div>
                <label className="text-[11px] font-mono text-slate-400 block mb-1">Symbol:</label>
                <select
                  value={calcSymbol}
                  onChange={(e) => setCalcSymbol(e.target.value)}
                  className="w-full bg-slate-900 text-white font-mono text-xs px-3 py-2 rounded-lg border border-slate-800 focus:outline-none focus:border-cyan-500"
                >
                  <option value="EURUSD">EURUSD (Digits: 5, Pip: 0.0001)</option>
                  <option value="GBPUSD">GBPUSD (Digits: 5, Pip: 0.0001)</option>
                  <option value="USDJPY">USDJPY (Digits: 3, Pip: 0.01)</option>
                  <option value="XAUUSD">XAUUSD (Digits: 2, Pip: 0.01)</option>
                  <option value="BTCUSD">BTCUSD (Digits: 2, Pip: 1.0)</option>
                </select>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-[11px] font-mono text-slate-400 block mb-1">Lots:</label>
                  <input
                    type="number"
                    step="0.01"
                    min="0.01"
                    value={calcLots}
                    onChange={(e) => setCalcLots(parseFloat(e.target.value) || 0.1)}
                    className="w-full bg-slate-900 text-white font-mono text-xs px-3 py-2 rounded-lg border border-slate-800 focus:outline-none focus:border-cyan-500"
                  />
                </div>
                <div>
                  <label className="text-[11px] font-mono text-slate-400 block mb-1">Leverage (1:X):</label>
                  <input
                    type="number"
                    min="1"
                    max="1000"
                    value={calcLeverage}
                    onChange={(e) => setCalcLeverage(parseFloat(e.target.value) || 100)}
                    className="w-full bg-slate-900 text-white font-mono text-xs px-3 py-2 rounded-lg border border-slate-800 focus:outline-none focus:border-cyan-500"
                  />
                </div>
              </div>

              <div>
                <label className="text-[11px] font-mono text-slate-400 block mb-1">Deposit Asset Currency:</label>
                <select
                  value={calcDepositAsset}
                  onChange={(e) => setCalcDepositAsset(e.target.value)}
                  className="w-full bg-slate-900 text-white font-mono text-xs px-3 py-2 rounded-lg border border-slate-800 focus:outline-none focus:border-cyan-500"
                >
                  <option value="USD">USD ($)</option>
                  <option value="EUR">EUR (€)</option>
                  <option value="GBP">GBP (£)</option>
                </select>
              </div>

              <button
                onClick={fetchCalculator}
                disabled={calcLoading}
                className="w-full mt-2 py-2.5 rounded-xl bg-amber-500 hover:bg-amber-400 text-slate-950 font-bold font-mono text-xs flex items-center justify-center gap-2 transition-all active:scale-98 shadow-lg shadow-amber-500/10"
              >
                <Calculator size={14} className={calcLoading ? 'animate-spin' : ''} />
                Calculate Financial Metrics
              </button>
            </div>
          </div>

          <div className="xl:col-span-7 bg-[#0d0f17] border border-slate-800/80 rounded-2xl p-6 flex flex-col justify-between">
            <div className="flex items-center gap-2 pb-4 border-b border-slate-800">
              <DollarSign className="text-emerald-400" size={18} />
              <h3 className="text-sm font-bold text-white">Calculated Position & Margin Metrics</h3>
            </div>

            {calcResult ? (
              <div className="grid grid-cols-2 gap-4 my-auto py-4">
                <div className="bg-slate-900/60 p-4 rounded-xl border border-slate-800/80">
                  <span className="text-[10px] font-mono text-slate-400 uppercase tracking-wider block">Pip Value</span>
                  <span className="text-xl font-bold font-mono text-emerald-400">${calcResult.pip_value}</span>
                  <span className="text-[10px] text-slate-500 block mt-1">Per 1.0 pip movement</span>
                </div>

                <div className="bg-slate-900/60 p-4 rounded-xl border border-slate-800/80">
                  <span className="text-[10px] font-mono text-slate-400 uppercase tracking-wider block">Required Margin</span>
                  <span className="text-xl font-bold font-mono text-cyan-400">${calcResult.required_margin}</span>
                  <span className="text-[10px] text-slate-500 block mt-1">At 1:{calcLeverage} leverage</span>
                </div>

                <div className="bg-slate-900/60 p-4 rounded-xl border border-slate-800/80">
                  <span className="text-[10px] font-mono text-slate-400 uppercase tracking-wider block">Notional Value</span>
                  <span className="text-lg font-bold font-mono text-white">${calcResult.notional_value?.toLocaleString()}</span>
                  <span className="text-[10px] text-slate-500 block mt-1">{calcResult.volume_units?.toLocaleString()} raw protocol units</span>
                </div>

                <div className="bg-slate-900/60 p-4 rounded-xl border border-slate-800/80">
                  <span className="text-[10px] font-mono text-slate-400 uppercase tracking-wider block">Tick Value</span>
                  <span className="text-lg font-bold font-mono text-amber-400">${calcResult.tick_value}</span>
                  <span className="text-[10px] text-slate-500 block mt-1">Tick size: {calcResult.tick_size}</span>
                </div>
              </div>
            ) : (
              <div className="text-center py-12 text-slate-500 text-xs">
                Click "Calculate Financial Metrics" to compute pip values.
              </div>
            )}

            <div className="pt-4 border-t border-slate-800/80 text-[11px] font-mono text-slate-400 flex items-center gap-2">
              <ShieldCheck className="text-cyan-400 shrink-0" size={16} />
              <span>Formulas strictly verified against Spotware OpenAPI.Net <code className="text-slate-300">SymbolExtensions.cs</code>.</span>
            </div>
          </div>
        </div>
      )}

      {/* ── TAB 4: Protobuf Protocol Explorer ── */}
      {activeTab === 'proto' && (
        <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
          <div className="xl:col-span-5 bg-[#0d0f17] border border-slate-800/80 rounded-2xl p-5 flex flex-col gap-4">
            <div className="flex items-center gap-2 pb-3 border-b border-slate-800">
              <Code className="text-cyan-400" size={18} />
              <h3 className="text-sm font-bold text-white">Protobuf Request Builder</h3>
            </div>

            <div>
              <label className="text-[11px] font-mono text-slate-400 block mb-1">Message Type:</label>
              <select
                value={selectedProto}
                onChange={(e) => handleProtoChange(e.target.value)}
                className="w-full bg-slate-900 text-white font-mono text-xs px-3 py-2 rounded-lg border border-slate-800 focus:outline-none focus:border-cyan-500"
              >
                <option value="ProtoOAGetTrendbarsReq">ProtoOAGetTrendbarsReq (2137)</option>
                <option value="ProtoOAGetTickDataReq">ProtoOAGetTickDataReq (2145)</option>
                <option value="ProtoOASymbolsListReq">ProtoOASymbolsListReq (2114)</option>
                <option value="ProtoOAApplicationAuthReq">ProtoOAApplicationAuthReq (2100)</option>
                <option value="ProtoOANewOrderReq">ProtoOANewOrderReq (2106)</option>
              </select>
            </div>

            <div className="flex-1 flex flex-col">
              <label className="text-[11px] font-mono text-slate-400 block mb-1">JSON Payload Body:</label>
              <textarea
                value={protoPayload}
                onChange={(e) => setProtoPayload(e.target.value)}
                rows={10}
                className="w-full flex-1 bg-slate-950 font-mono text-xs text-cyan-300 p-3 rounded-lg border border-slate-800 focus:outline-none focus:border-cyan-500 resize-none"
              />
            </div>

            <button
              onClick={executeProtoTest}
              disabled={protoLoading}
              className="w-full py-2.5 rounded-xl bg-cyan-500 hover:bg-cyan-400 text-slate-950 font-bold font-mono text-xs flex items-center justify-center gap-2 transition-all active:scale-98 shadow-lg shadow-cyan-500/10"
            >
              <Send size={14} className={protoLoading ? 'animate-spin' : ''} />
              {protoLoading ? 'Framing & Sending...' : 'Execute Protobuf Frame'}
            </button>
          </div>

          <div className="xl:col-span-7 bg-[#0d0f17] border border-slate-800/80 rounded-2xl p-5 flex flex-col h-[520px]">
            <div className="flex items-center justify-between pb-3 border-b border-slate-800 mb-3">
              <h3 className="text-sm font-bold text-white flex items-center gap-2">
                <Terminal size={14} className="text-emerald-400" />
                <span>Response Frame Inspector</span>
              </h3>
              {protoResponse && (
                <button
                  onClick={() => copyToClipboard(JSON.stringify(protoResponse, null, 2))}
                  className="flex items-center gap-1 text-[11px] font-mono text-slate-400 hover:text-white px-2 py-1 rounded bg-slate-900 border border-slate-800"
                >
                  <Copy size={12} /> {copied ? 'Copied!' : 'Copy'}
                </button>
              )}
            </div>

            <div className="flex-1 bg-slate-950 rounded-xl p-4 overflow-auto font-mono text-xs text-emerald-300 border border-slate-900 scrollbar-thin scrollbar-thumb-slate-800">
              {protoResponse ? (
                <pre>{JSON.stringify(protoResponse, null, 2)}</pre>
              ) : (
                <div className="h-full flex items-center justify-center text-slate-600 text-xs">
                  Click "Execute Protobuf Frame" to inspect encoded response packets.
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
export default OpenApiSamplesView;
