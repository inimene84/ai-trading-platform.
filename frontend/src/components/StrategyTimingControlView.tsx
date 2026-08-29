import React, { useState, useEffect, useCallback } from 'react';
import {
  Sliders,
  Play,
  RefreshCw,
  Clock,
  Zap,
  TrendingUp,
  TrendingDown,
  Shield,
  Layers,
  Activity,
  CheckCircle2,
  AlertCircle,
  Download,
  Copy,
  ChevronRight,
  Flame,
  ArrowUpRight,
  ArrowDownRight,
  Radio,
  Workflow,
  Sparkles,
  Info,
  Timer,
} from 'lucide-react';
import { apiService } from '../services/apiService';
import { cn } from '../lib/utils';

export const StrategyTimingControlView: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'candidates' | 'strategies' | 'timing' | 'n8n'>('candidates');
  const [candidates, setCandidates] = useState<any[]>([]);
  const [readySignals, setReadySignals] = useState<any[]>([]);
  const [timingConfig, setTimingConfig] = useState<any>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [scanStatus, setScanStatus] = useState<string | null>(null);
  const [executionStatus, setExecutionStatus] = useState<{ [key: string]: string }>({});
  const [copiedWorkflow, setCopiedWorkflow] = useState<string | null>(null);

  const fetchAllData = useCallback(async () => {
    try {
      const [candsRes, readyRes, configRes]: any = await Promise.all([
        apiService.getSignalsCandidates(),
        apiService.getReadySignals(),
        apiService.getTimingConfig(),
      ]);

      if (candsRes && candsRes.candidates) {
        setCandidates(candsRes.candidates);
      }
      if (readyRes && readyRes.signals) {
        setReadySignals(readyRes.signals);
      }
      if (configRes && configRes.config) {
        setTimingConfig(configRes.config);
      }
    } catch (err) {
      console.warn('Error fetching signals data', err);
    }
  }, []);

  useEffect(() => {
    fetchAllData();
    const interval = setInterval(fetchAllData, 8000);
    return () => clearInterval(interval);
  }, [fetchAllData]);

  // Scan Markets Trigger
  const triggerMarketScan = async () => {
    setIsLoading(true);
    setScanStatus('Scanning Forex & Crypto universes for technical setups...');
    try {
      const res: any = await apiService.scanMarkets();
      setScanStatus(`Scan complete! Found ${res.candidates_count || 0} trade candidates.`);
      await fetchAllData();
    } catch (err: any) {
      setScanStatus(`Scan error: ${err.message}`);
    } finally {
      setIsLoading(false);
      setTimeout(() => setScanStatus(null), 5000);
    }
  };

  // Scan News Trigger
  const triggerNewsScan = async () => {
    setIsLoading(true);
    setScanStatus('Correlating Economic Calendar events & sentiment streams...');
    try {
      const res: any = await apiService.scanNewsSignals(60);
      setScanStatus(`News scan complete! Armed ${res.candidates_count || 0} macro event setups.`);
      await fetchAllData();
    } catch (err: any) {
      setScanStatus(`News scan error: ${err.message}`);
    } finally {
      setIsLoading(false);
      setTimeout(() => setScanStatus(null), 5000);
    }
  };

  // Execute candidate signal
  const executeCandidate = async (candidateId: string, force = false) => {
    setExecutionStatus((prev) => ({ ...prev, [candidateId]: 'Executing...' }));
    try {
      const res: any = await apiService.executeSignalCandidate(candidateId, force);
      if (res && res.success) {
        setExecutionStatus((prev) => ({ ...prev, [candidateId]: 'Executed successfully!' }));
        await fetchAllData();
      } else {
        setExecutionStatus((prev) => ({ ...prev, [candidateId]: `Failed: ${res.error || 'Error'}` }));
      }
    } catch (err: any) {
      setExecutionStatus((prev) => ({ ...prev, [candidateId]: `Error: ${err.message}` }));
    }
  };

  // Update timing config
  const handleConfigChange = async (key: string, val: any) => {
    if (!timingConfig) return;
    const newConfig = { ...timingConfig, [key]: val };
    setTimingConfig(newConfig);
    try {
      await apiService.updateTimingConfig(newConfig);
    } catch (e) {
      console.warn('Failed to update config', e);
    }
  };

  const handleStrategyToggle = async (stratKey: string) => {
    if (!timingConfig) return;
    const current = timingConfig.strategies_enabled || {};
    const newStrats = { ...current, [stratKey]: !current[stratKey] };
    const newConfig = { ...timingConfig, strategies_enabled: newStrats };
    setTimingConfig(newConfig);
    try {
      await apiService.updateTimingConfig(newConfig);
    } catch (e) {
      console.warn('Failed to update strategy toggle', e);
    }
  };

  const copyWorkflow = (workflowName: string, jsonContent: string) => {
    navigator.clipboard.writeText(jsonContent);
    setCopiedWorkflow(workflowName);
    setTimeout(() => setCopiedWorkflow(null), 2500);
  };

  return (
    <div className="flex-1 flex flex-col h-full bg-[#08090d] text-slate-100 overflow-y-auto p-4 md:p-6 select-none">
      {/* ── Header ── */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-5 border-b border-slate-800/80 mb-6">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="px-2 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-[10px] font-mono font-bold tracking-wider uppercase">
              Layered Multi-Asset Engine
            </span>
            <span className="flex items-center gap-1 text-[11px] font-mono text-cyan-400">
              <Activity size={12} /> {readySignals.length} Ready for Execution
            </span>
          </div>
          <h1 className="text-xl md:text-2xl font-bold text-white flex items-center gap-2">
            Strategy & Execution Timing Control
          </h1>
          <p className="text-xs text-slate-400 mt-0.5">
            Orchestrate candidate generation, fine-tune Pre/At/Post execution timing windows, and monitor live trading queues.
          </p>
        </div>

        {/* Scan Action Buttons & Tab Switcher */}
        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={triggerMarketScan}
            disabled={isLoading}
            className="px-3 py-1.5 rounded-xl bg-cyan-500 hover:bg-cyan-400 text-slate-950 text-xs font-mono font-bold flex items-center gap-1.5 transition-all active:scale-95 shadow-md shadow-cyan-500/10"
          >
            <Activity size={13} className={isLoading ? 'animate-spin' : ''} />
            Scan Markets
          </button>
          <button
            onClick={triggerNewsScan}
            disabled={isLoading}
            className="px-3 py-1.5 rounded-xl bg-amber-500 hover:bg-amber-400 text-slate-950 text-xs font-mono font-bold flex items-center gap-1.5 transition-all active:scale-95 shadow-md shadow-amber-500/10"
          >
            <Zap size={13} className={isLoading ? 'animate-spin' : ''} />
            Scan Macro News
          </button>

          {/* Tab Navigation */}
          <div className="flex items-center bg-slate-900/90 p-0.5 rounded-xl border border-slate-800">
            <button
              onClick={() => setActiveTab('candidates')}
              className={cn(
                'px-3 py-1.5 text-xs font-mono font-medium rounded-lg transition-all',
                activeTab === 'candidates'
                  ? 'bg-slate-800 text-white font-bold border border-slate-700'
                  : 'text-slate-400 hover:text-white'
              )}
            >
              Candidates ({candidates.length})
            </button>
            <button
              onClick={() => setActiveTab('strategies')}
              className={cn(
                'px-3 py-1.5 text-xs font-mono font-medium rounded-lg transition-all',
                activeTab === 'strategies'
                  ? 'bg-slate-800 text-white font-bold border border-slate-700'
                  : 'text-slate-400 hover:text-white'
              )}
            >
              Strategies
            </button>
            <button
              onClick={() => setActiveTab('timing')}
              className={cn(
                'px-3 py-1.5 text-xs font-mono font-medium rounded-lg transition-all',
                activeTab === 'timing'
                  ? 'bg-slate-800 text-white font-bold border border-slate-700'
                  : 'text-slate-400 hover:text-white'
              )}
            >
              Timing & Risk
            </button>
            <button
              onClick={() => setActiveTab('n8n')}
              className={cn(
                'px-3 py-1.5 text-xs font-mono font-medium rounded-lg transition-all',
                activeTab === 'n8n'
                  ? 'bg-slate-800 text-white font-bold border border-slate-700'
                  : 'text-slate-400 hover:text-white'
              )}
            >
              n8n Workflows
            </button>
          </div>
        </div>
      </div>

      {scanStatus && (
        <div className="mb-4 p-3 rounded-xl bg-cyan-950/40 border border-cyan-800/60 text-cyan-300 font-mono text-xs flex items-center gap-2 animate-pulse">
          <Info size={14} />
          <span>{scanStatus}</span>
        </div>
      )}

      {/* ── TAB 1: Live Trade Candidates Queue ── */}
      {activeTab === 'candidates' && (
        <div className="flex flex-col gap-4">
          <div className="bg-[#0d0f17] border border-slate-800/80 rounded-2xl p-5">
            <div className="flex items-center justify-between pb-4 border-b border-slate-800 mb-4">
              <h3 className="text-sm font-bold text-white flex items-center gap-2">
                <Timer size={16} className="text-cyan-400" />
                <span>Trade Candidates & Timing Queue</span>
                <span className="text-xs font-mono text-slate-500 font-normal">({candidates.length} total generated)</span>
              </h3>
              <button
                onClick={fetchAllData}
                className="p-1.5 bg-slate-900 text-slate-400 hover:text-white rounded-lg border border-slate-800"
              >
                <RefreshCw size={13} />
              </button>
            </div>

            {candidates.length > 0 ? (
              <div className="overflow-x-auto scrollbar-thin scrollbar-thumb-slate-800">
                <table className="w-full text-left font-mono text-xs">
                  <thead className="bg-slate-900/80 text-slate-400 text-[10px] uppercase">
                    <tr>
                      <th className="py-2.5 px-3">Status</th>
                      <th className="py-2.5 px-3">Symbol / Broker</th>
                      <th className="py-2.5 px-3">Strategy</th>
                      <th className="py-2.5 px-3">Side</th>
                      <th className="py-2.5 px-3 text-right">Entry</th>
                      <th className="py-2.5 px-3 text-right">SL / TP</th>
                      <th className="py-2.5 px-3 text-right">Lots / Size</th>
                      <th className="py-2.5 px-3">Timing Mode</th>
                      <th className="py-2.5 px-3 text-center">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60">
                    {candidates.map((cand) => {
                      const isReady = cand.status === 'READY';
                      const isExecuted = cand.status === 'EXECUTED';
                      const isPending = cand.status === 'PENDING';
                      const isBuy = cand.direction === 'BUY';

                      return (
                        <tr key={cand.id} className="hover:bg-slate-800/30 transition-colors">
                          <td className="py-2.5 px-3">
                            <span
                              className={cn(
                                'px-2 py-0.5 rounded text-[10px] font-bold inline-block',
                                isReady
                                  ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 animate-pulse'
                                  : isExecuted
                                  ? 'bg-cyan-500/20 text-cyan-400 border border-cyan-500/40'
                                  : isPending
                                  ? 'bg-amber-500/20 text-amber-400 border border-amber-500/40'
                                  : 'bg-slate-800 text-slate-400'
                              )}
                            >
                              {cand.status}
                            </span>
                          </td>
                          <td className="py-2.5 px-3">
                            <span className="font-bold text-white block">{cand.symbol}</span>
                            <span className="text-[10px] text-slate-500 uppercase">{cand.broker}</span>
                          </td>
                          <td className="py-2.5 px-3">
                            <span className="text-slate-300 block">{cand.strategy}</span>
                            <span className="text-[10px] text-slate-500 truncate max-w-xs block">{cand.reason}</span>
                          </td>
                          <td className="py-2.5 px-3">
                            <span
                              className={cn(
                                'font-bold px-1.5 py-0.5 rounded text-[11px]',
                                isBuy ? 'text-emerald-400 bg-emerald-950/60' : 'text-rose-400 bg-rose-950/60'
                              )}
                            >
                              {cand.direction}
                            </span>
                          </td>
                          <td className="py-2.5 px-3 text-right font-bold text-white">
                            {cand.entry_price?.toFixed(5)}
                          </td>
                          <td className="py-2.5 px-3 text-right">
                            <span className="text-rose-400 block text-[11px]">{cand.stop_loss?.toFixed(5)}</span>
                            <span className="text-emerald-400 block text-[11px]">{cand.take_profit?.toFixed(5)}</span>
                          </td>
                          <td className="py-2.5 px-3 text-right">
                            <span className="text-cyan-400 font-bold block">{cand.sizing?.lots || cand.sizing?.quantity}</span>
                            <span className="text-[10px] text-slate-500 block">${cand.sizing?.risk_usd} risk</span>
                          </td>
                          <td className="py-2.5 px-3">
                            <span className="px-2 py-0.5 rounded bg-slate-900 border border-slate-800 text-slate-300 text-[10px]">
                              {cand.timing_mode}
                            </span>
                          </td>
                          <td className="py-2.5 px-3 text-center">
                            {isExecuted ? (
                              <span className="text-emerald-400 text-xs flex items-center justify-center gap-1">
                                <CheckCircle2 size={13} /> Filled
                              </span>
                            ) : (
                              <button
                                onClick={() => executeCandidate(cand.id, true)}
                                className={cn(
                                  'px-2.5 py-1 rounded text-xs font-mono font-bold transition-all',
                                  isReady
                                    ? 'bg-emerald-500 hover:bg-emerald-400 text-slate-950 shadow-md shadow-emerald-500/10'
                                    : 'bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700'
                                )}
                              >
                                {executionStatus[cand.id] || (isReady ? 'Execute Now' : 'Force Fire')}
                              </button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="py-16 text-center text-slate-500 text-xs flex flex-col items-center justify-center gap-3">
                <Timer size={32} className="text-slate-700" />
                <span>No active candidates. Click "Scan Markets" or "Scan Macro News" to generate setups.</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── TAB 2: Strategy Matrix ── */}
      {activeTab === 'strategies' && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Momentum */}
          <div className="bg-[#0d0f17] border border-slate-800/80 rounded-2xl p-5 flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between pb-3 border-b border-slate-800 mb-3">
                <div className="flex items-center gap-2">
                  <Flame className="text-cyan-400" size={18} />
                  <h3 className="text-sm font-bold text-white">1. Momentum Trend Pulse</h3>
                </div>
                <button
                  onClick={() => handleStrategyToggle('momentum')}
                  className={cn(
                    'px-2.5 py-1 rounded-full text-[10px] font-mono font-bold transition-all',
                    timingConfig?.strategies_enabled?.momentum
                      ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40'
                      : 'bg-slate-800 text-slate-500'
                  )}
                >
                  {timingConfig?.strategies_enabled?.momentum ? 'ACTIVE' : 'DISABLED'}
                </button>
              </div>
              <p className="text-xs text-slate-400 leading-relaxed mb-4">
                Enters immediately following a directional breakout or strong news reaction once the first 1-2 confirmation bars print.
              </p>
              <div className="space-y-2 text-[11px] font-mono text-slate-400 bg-slate-900/60 p-3 rounded-xl border border-slate-800">
                <div className="flex justify-between"><span>Trigger:</span> <span className="text-slate-200">EMA9/21 cross + RSI 52-72</span></div>
                <div className="flex justify-between"><span>Default Timing:</span> <span className="text-cyan-400">POST_REACTION</span></div>
                <div className="flex justify-between"><span>Stop-Loss:</span> <span className="text-rose-400">1.5 × ATR</span></div>
                <div className="flex justify-between"><span>Take-Profit:</span> <span className="text-emerald-400">2.5 × ATR</span></div>
              </div>
            </div>
          </div>

          {/* Fade */}
          <div className="bg-[#0d0f17] border border-slate-800/80 rounded-2xl p-5 flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between pb-3 border-b border-slate-800 mb-3">
                <div className="flex items-center gap-2">
                  <TrendingDown className="text-amber-400" size={18} />
                  <h3 className="text-sm font-bold text-white">2. Fade Overextension (Contrarian)</h3>
                </div>
                <button
                  onClick={() => handleStrategyToggle('fade')}
                  className={cn(
                    'px-2.5 py-1 rounded-full text-[10px] font-mono font-bold transition-all',
                    timingConfig?.strategies_enabled?.fade
                      ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40'
                      : 'bg-slate-800 text-slate-500'
                  )}
                >
                  {timingConfig?.strategies_enabled?.fade ? 'ACTIVE' : 'DISABLED'}
                </button>
              </div>
              <p className="text-xs text-slate-400 leading-relaxed mb-4">
                Identifies parabolic moves into key extremes and takes counter-trend mean-reversion positions back toward the 21 EMA.
              </p>
              <div className="space-y-2 text-[11px] font-mono text-slate-400 bg-slate-900/60 p-3 rounded-xl border border-slate-800">
                <div className="flex justify-between"><span>Trigger:</span> <span className="text-slate-200">RSI ≥ 75 or RSI ≤ 25</span></div>
                <div className="flex justify-between"><span>Default Timing:</span> <span className="text-amber-400">POST_REACTION</span></div>
                <div className="flex justify-between"><span>Stop-Loss:</span> <span className="text-rose-400">Recent Peak + 0.5 ATR</span></div>
                <div className="flex justify-between"><span>Take-Profit:</span> <span className="text-emerald-400">EMA21 Mean</span></div>
              </div>
            </div>
          </div>

          {/* Straddle */}
          <div className="bg-[#0d0f17] border border-slate-800/80 rounded-2xl p-5 flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between pb-3 border-b border-slate-800 mb-3">
                <div className="flex items-center gap-2">
                  <Layers className="text-indigo-400" size={18} />
                  <h3 className="text-sm font-bold text-white">3. Pre-Event Volatility Straddle</h3>
                </div>
                <button
                  onClick={() => handleStrategyToggle('straddle')}
                  className={cn(
                    'px-2.5 py-1 rounded-full text-[10px] font-mono font-bold transition-all',
                    timingConfig?.strategies_enabled?.straddle
                      ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40'
                      : 'bg-slate-800 text-slate-500'
                  )}
                >
                  {timingConfig?.strategies_enabled?.straddle ? 'ACTIVE' : 'DISABLED'}
                </button>
              </div>
              <p className="text-xs text-slate-400 leading-relaxed mb-4">
                Pre-arms bracket orders above and below tight consolidation ranges 15 minutes before scheduled high-impact events (NFP, CPI).
              </p>
              <div className="space-y-2 text-[11px] font-mono text-slate-400 bg-slate-900/60 p-3 rounded-xl border border-slate-800">
                <div className="flex justify-between"><span>Trigger:</span> <span className="text-slate-200">Volatility &lt; 0.25% compression</span></div>
                <div className="flex justify-between"><span>Default Timing:</span> <span className="text-indigo-400">PRE_EVENT</span></div>
                <div className="flex justify-between"><span>Brackets:</span> <span className="text-slate-200">Range High / Low + 0.4 ATR</span></div>
              </div>
            </div>
          </div>

          {/* Slingshot */}
          <div className="bg-[#0d0f17] border border-slate-800/80 rounded-2xl p-5 flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between pb-3 border-b border-slate-800 mb-3">
                <div className="flex items-center gap-2">
                  <Zap className="text-emerald-400" size={18} />
                  <h3 className="text-sm font-bold text-white">4. Slingshot Pullback Re-entry</h3>
                </div>
                <button
                  onClick={() => handleStrategyToggle('slingshot')}
                  className={cn(
                    'px-2.5 py-1 rounded-full text-[10px] font-mono font-bold transition-all',
                    timingConfig?.strategies_enabled?.slingshot
                      ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40'
                      : 'bg-slate-800 text-slate-500'
                  )}
                >
                  {timingConfig?.strategies_enabled?.slingshot ? 'ACTIVE' : 'DISABLED'}
                </button>
              </div>
              <p className="text-xs text-slate-400 leading-relaxed mb-4">
                Waits for deep pullbacks against the dominant trend to exhaust, then enters at discounted prices on the M5/M15 candle close.
              </p>
              <div className="space-y-2 text-[11px] font-mono text-slate-400 bg-slate-900/60 p-3 rounded-xl border border-slate-800">
                <div className="flex justify-between"><span>Trigger:</span> <span className="text-slate-200">Wick rejection in trend</span></div>
                <div className="flex justify-between"><span>Default Timing:</span> <span className="text-emerald-400">BAR_CLOSE</span></div>
                <div className="flex justify-between"><span>Stop-Loss:</span> <span className="text-rose-400">Wick Extreme + 0.3 ATR</span></div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── TAB 3: Timing & Risk Configurator ── */}
      {activeTab === 'timing' && timingConfig && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Timing Windows */}
          <div className="bg-[#0d0f17] border border-slate-800/80 rounded-2xl p-5 flex flex-col gap-4">
            <div className="flex items-center gap-2 pb-3 border-b border-slate-800">
              <Clock size={16} className="text-cyan-400" />
              <h3 className="text-sm font-bold text-white">Execution Timing Windows</h3>
            </div>

            <div className="space-y-4">
              <div>
                <label className="text-xs font-mono text-slate-400 flex justify-between mb-1">
                  <span>Pre-Event Observation Window:</span>
                  <strong className="text-white">{timingConfig.pre_event_window_min} mins</strong>
                </label>
                <input
                  type="range"
                  min="5"
                  max="60"
                  value={timingConfig.pre_event_window_min}
                  onChange={(e) => handleConfigChange('pre_event_window_min', parseInt(e.target.value))}
                  className="w-full accent-cyan-500 cursor-pointer"
                />
              </div>

              <div>
                <label className="text-xs font-mono text-slate-400 flex justify-between mb-1">
                  <span>Post-Reaction Entry Delay:</span>
                  <strong className="text-amber-400">{timingConfig.post_reaction_delay_min} mins</strong>
                </label>
                <input
                  type="range"
                  min="0"
                  max="15"
                  value={timingConfig.post_reaction_delay_min}
                  onChange={(e) => handleConfigChange('post_reaction_delay_min', parseInt(e.target.value))}
                  className="w-full accent-amber-500 cursor-pointer"
                />
                <span className="text-[10px] text-slate-500 mt-0.5 block">Delays entry after news to allow spread widening to settle.</span>
              </div>

              <div>
                <label className="text-xs font-mono text-slate-400 flex justify-between mb-1">
                  <span>Post-Reaction Window Duration:</span>
                  <strong className="text-white">{timingConfig.post_reaction_window_min} mins</strong>
                </label>
                <input
                  type="range"
                  min="2"
                  max="30"
                  value={timingConfig.post_reaction_window_min}
                  onChange={(e) => handleConfigChange('post_reaction_window_min', parseInt(e.target.value))}
                  className="w-full accent-cyan-500 cursor-pointer"
                />
              </div>
            </div>
          </div>

          {/* Risk & Spread Gates */}
          <div className="bg-[#0d0f17] border border-slate-800/80 rounded-2xl p-5 flex flex-col gap-4">
            <div className="flex items-center gap-2 pb-3 border-b border-slate-800">
              <Shield size={16} className="text-emerald-400" />
              <h3 className="text-sm font-bold text-white">Risk Sizing & Quality Gates</h3>
            </div>

            <div className="space-y-4">
              <div>
                <label className="text-xs font-mono text-slate-400 flex justify-between mb-1">
                  <span>Default Risk Per Trade:</span>
                  <strong className="text-emerald-400">{timingConfig.default_risk_pct}% of equity</strong>
                </label>
                <input
                  type="range"
                  step="0.1"
                  min="0.1"
                  max="3.0"
                  value={timingConfig.default_risk_pct}
                  onChange={(e) => handleConfigChange('default_risk_pct', parseFloat(e.target.value))}
                  className="w-full accent-emerald-500 cursor-pointer"
                />
              </div>

              <div>
                <label className="text-xs font-mono text-slate-400 flex justify-between mb-1">
                  <span>Max Allowable Spread:</span>
                  <strong className="text-white">{timingConfig.max_spread_pips} pips</strong>
                </label>
                <input
                  type="range"
                  step="0.1"
                  min="0.5"
                  max="10.0"
                  value={timingConfig.max_spread_pips}
                  onChange={(e) => handleConfigChange('max_spread_pips', parseFloat(e.target.value))}
                  className="w-full accent-cyan-500 cursor-pointer"
                />
              </div>

              <div>
                <label className="text-xs font-mono text-slate-400 flex justify-between mb-1">
                  <span>Account Equity Baseline:</span>
                  <strong className="text-white">${timingConfig.account_equity_override?.toLocaleString()}</strong>
                </label>
                <input
                  type="number"
                  step="500"
                  value={timingConfig.account_equity_override}
                  onChange={(e) => handleConfigChange('account_equity_override', parseFloat(e.target.value) || 10000)}
                  className="w-full bg-slate-900 text-white font-mono text-xs px-3 py-1.5 rounded-lg border border-slate-800 focus:outline-none focus:border-cyan-500"
                />
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── TAB 4: Exportable n8n Workflows ── */}
      {activeTab === 'n8n' && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {/* Workflow 1 */}
          <div className="bg-[#0d0f17] border border-slate-800/80 rounded-2xl p-5 flex flex-col justify-between">
            <div>
              <div className="flex items-center gap-2 pb-3 border-b border-slate-800 mb-3">
                <Workflow className="text-cyan-400" size={18} />
                <h3 className="text-sm font-bold text-white">01. Market Scanner</h3>
              </div>
              <p className="text-xs text-slate-400 mb-4">
                Runs on a 5-minute cron schedule. Polls the backend scanner across Forex & Crypto pairs to discover fresh technical setups.
              </p>
              <div className="bg-slate-950 p-3 rounded-xl border border-slate-900 font-mono text-[11px] text-slate-400 mb-4">
                <span>Trigger: </span><strong className="text-white">5m Cron</strong><br />
                <span>Endpoint: </span><strong className="text-cyan-400">/api/signals/scan-markets</strong>
              </div>
            </div>
            <button
              onClick={() => copyWorkflow('01', JSON.stringify({ name: '01 - Market Scanner', endpoint: '/api/signals/scan-markets' }, null, 2))}
              className="w-full py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 font-mono text-xs font-bold flex items-center justify-center gap-2 border border-slate-700 transition-all"
            >
              <Copy size={13} /> {copiedWorkflow === '01' ? 'Copied to Clipboard!' : 'Copy Workflow JSON'}
            </button>
          </div>

          {/* Workflow 2 */}
          <div className="bg-[#0d0f17] border border-slate-800/80 rounded-2xl p-5 flex flex-col justify-between">
            <div>
              <div className="flex items-center gap-2 pb-3 border-b border-slate-800 mb-3">
                <Zap className="text-amber-400" size={18} />
                <h3 className="text-sm font-bold text-white">02. News & Macro Scanner</h3>
              </div>
              <p className="text-xs text-slate-400 mb-4">
                Runs every 1 minute. Correlates upcoming Forex Factory economic events with live RSS sentiment to arm high-impact event candidates.
              </p>
              <div className="bg-slate-950 p-3 rounded-xl border border-slate-900 font-mono text-[11px] text-slate-400 mb-4">
                <span>Trigger: </span><strong className="text-white">1m Cron</strong><br />
                <span>Endpoint: </span><strong className="text-amber-400">/api/signals/scan-news</strong>
              </div>
            </div>
            <button
              onClick={() => copyWorkflow('02', JSON.stringify({ name: '02 - News Scanner', endpoint: '/api/signals/scan-news' }, null, 2))}
              className="w-full py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 font-mono text-xs font-bold flex items-center justify-center gap-2 border border-slate-700 transition-all"
            >
              <Copy size={13} /> {copiedWorkflow === '02' ? 'Copied to Clipboard!' : 'Copy Workflow JSON'}
            </button>
          </div>

          {/* Workflow 3 */}
          <div className="bg-[#0d0f17] border border-slate-800/80 rounded-2xl p-5 flex flex-col justify-between">
            <div>
              <div className="flex items-center gap-2 pb-3 border-b border-slate-800 mb-3">
                <Timer className="text-emerald-400" size={18} />
                <h3 className="text-sm font-bold text-white">03. Execution Scheduler</h3>
              </div>
              <p className="text-xs text-slate-400 mb-4">
                Runs every 30 seconds. Checks for candidates inside their active timing window and automatically fires orders through cTrader / Binance.
              </p>
              <div className="bg-slate-950 p-3 rounded-xl border border-slate-900 font-mono text-[11px] text-slate-400 mb-4">
                <span>Trigger: </span><strong className="text-white">30s Poller</strong><br />
                <span>Endpoint: </span><strong className="text-emerald-400">/api/signals/execute-candidate</strong>
              </div>
            </div>
            <button
              onClick={() => copyWorkflow('03', JSON.stringify({ name: '03 - Execution Scheduler', endpoint: '/api/signals/ready-for-execution' }, null, 2))}
              className="w-full py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 font-mono text-xs font-bold flex items-center justify-center gap-2 border border-slate-700 transition-all"
            >
              <Copy size={13} /> {copiedWorkflow === '03' ? 'Copied to Clipboard!' : 'Copy Workflow JSON'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
export default StrategyTimingControlView;
