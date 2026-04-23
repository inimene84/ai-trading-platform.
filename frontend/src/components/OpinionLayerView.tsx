/**
 * Opinion Layer View
 * Multi-agent market analysis dashboard.
 * Shows each agent's vote, confidence bars, and final aggregated decision.
 */

import React, { useState, useCallback, useMemo } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import {
  BrainCircuit,
  TrendingUp,
  TrendingDown,
  Minus,
  Activity,
  Globe,
  AlertTriangle,
  Sparkles,
  Loader2,
  Play,
  RefreshCw,
  Users,
  BarChart3,
  MessageSquare,
  Zap,
  Clock,
} from 'lucide-react';
import { cn } from '../lib/utils';
import { apiService } from '../services/apiService';

interface AgentOpinion {
  agent: string;
  signal: 'bullish' | 'bearish' | 'neutral';
  confidence: number;
  reasoning: string;
}

interface OpinionResult {
  symbol: string;
  direction: 'BUY' | 'SELL' | 'HOLD';
  confidence: number;
  reasoning: string;
  agent_opinions: AgentOpinion[];
  kronos: {
    signal?: string;
    predicted_change_pct?: number;
    predicted_close?: number;
    confidence?: number;
  };
  social: {
    direction?: string;
    sentiment_score?: number;
    article_count?: number;
  };
  alerts: Array<{
    alert_type: string;
    score: number;
  }>;
  timestamp: string;
}

const SIGNAL_COLORS = {
  bullish: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
  bearish: 'text-rose-400 bg-rose-500/10 border-rose-500/20',
  neutral: 'text-zinc-400 bg-zinc-500/10 border-zinc-500/20',
  BUY: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
  SELL: 'text-rose-400 bg-rose-500/10 border-rose-500/20',
  HOLD: 'text-zinc-400 bg-zinc-500/10 border-zinc-500/20',
};

const SIGNAL_ICONS = {
  bullish: TrendingUp,
  bearish: TrendingDown,
  neutral: Minus,
  BUY: TrendingUp,
  SELL: TrendingDown,
  HOLD: Minus,
};

const AGENT_ICONS: Record<string, any> = {
  technical_analyst: BarChart3,
  kronos_foundation: Sparkles,
  social_sentiment: MessageSquare,
  market_alerts: AlertTriangle,
  warren_buffett: Users,
  michael_burry: Users,
  stanley_druckenmiller: Users,
  cathie_wood: Users,
  peter_lynch: Users,
  charlie_munger: Users,
  nassim_taleb: Users,
  bill_ackman: Users,
  phil_fisher: Users,
  aswath_damodaran: Users,
  mohnish_pabrai: Users,
  rakesh_jhunjhunwala: Users,
};

const ConfidenceBar: React.FC<{ value: number; signal: string }> = ({ value, signal }) => {
  const pct = Math.round(value * 100);
  const colorClass = signal === 'bullish' || signal === 'BUY'
    ? 'bg-emerald-500'
    : signal === 'bearish' || signal === 'SELL'
    ? 'bg-rose-500'
    : 'bg-zinc-500';

  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-2 bg-zinc-800 rounded-full overflow-hidden">
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.8, ease: 'easeOut' }}
          className={cn("h-full rounded-full", colorClass)}
        />
      </div>
      <span className="text-[10px] font-mono text-zinc-400 w-8 text-right">{pct}%</span>
    </div>
  );
};

const AgentCard: React.FC<{ opinion: AgentOpinion; index: number }> = ({ opinion, index }) => {
  const Icon = AGENT_ICONS[opinion.agent] || Activity;
  const isPersona = !['technical_analyst', 'kronos_foundation', 'social_sentiment', 'market_alerts'].includes(opinion.agent);

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.05 }}
      className={cn(
        "p-3 rounded-xl border bg-zinc-900/40 backdrop-blur-sm",
        SIGNAL_COLORS[opinion.signal] || SIGNAL_COLORS.neutral
      )}
    >
      <div className="flex items-center gap-2 mb-2">
        <div className="w-7 h-7 rounded-lg bg-zinc-800 flex items-center justify-center">
          <Icon size={14} className="text-zinc-300" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-[11px] font-bold text-white truncate capitalize">
            {opinion.agent.replace(/_/g, ' ')}
          </p>
          {isPersona && (
            <p className="text-[9px] text-zinc-500 uppercase tracking-wider">Persona</p>
          )}
        </div>
        <div className={cn(
          "px-2 py-0.5 rounded-md text-[10px] font-black uppercase tracking-wider",
          SIGNAL_COLORS[opinion.signal] || SIGNAL_COLORS.neutral
        )}>
          {opinion.signal}
        </div>
      </div>
      <ConfidenceBar value={opinion.confidence} signal={opinion.signal} />
      {opinion.reasoning && (
        <p className="mt-2 text-[10px] text-zinc-400 leading-relaxed line-clamp-2">
          {opinion.reasoning}
        </p>
      )}
    </motion.div>
  );
};

const FinalDecisionCard: React.FC<{ result: OpinionResult }> = ({ result }) => {
  const Icon = SIGNAL_ICONS[result.direction] || Minus;
  const isBullish = result.direction === 'BUY';
  const isBearish = result.direction === 'SELL';

  return (
    <motion.div
      initial={{ scale: 0.95, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      className={cn(
        "relative overflow-hidden rounded-2xl border p-6",
        isBullish
          ? "border-emerald-500/30 bg-emerald-500/5"
          : isBearish
          ? "border-rose-500/30 bg-rose-500/5"
          : "border-zinc-700/50 bg-zinc-900/50"
      )}
    >
      {/* Background glow */}
      <div className={cn(
        "absolute -top-20 -right-20 w-40 h-40 rounded-full blur-3xl opacity-20",
        isBullish ? "bg-emerald-500" : isBearish ? "bg-rose-500" : "bg-zinc-500"
      )} />

      <div className="relative z-10">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <div className={cn(
              "w-12 h-12 rounded-xl flex items-center justify-center",
              isBullish ? "bg-emerald-500/20" : isBearish ? "bg-rose-500/20" : "bg-zinc-800"
            )}>
              <Icon size={24} className={cn(
                isBullish ? "text-emerald-400" : isBearish ? "text-rose-400" : "text-zinc-400"
              )} />
            </div>
            <div>
              <p className="text-[10px] text-zinc-500 uppercase tracking-widest font-bold">Final Decision</p>
              <h2 className={cn(
                "text-3xl font-black tracking-tight",
                isBullish ? "text-emerald-400" : isBearish ? "text-rose-400" : "text-zinc-300"
              )}>
                {result.direction}
              </h2>
            </div>
          </div>
          <div className="text-right">
            <p className="text-[10px] text-zinc-500 uppercase tracking-widest font-bold">Confidence</p>
            <p className={cn(
              "text-2xl font-black tabular-nums",
              isBullish ? "text-emerald-400" : isBearish ? "text-rose-400" : "text-zinc-300"
            )}>
              {Math.round(result.confidence * 100)}%
            </p>
          </div>
        </div>

        <div className="h-3 bg-zinc-800 rounded-full overflow-hidden mb-4">
          <motion.div
            initial={{ width: 0 }}
            animate={{ width: `${result.confidence * 100}%` }}
            transition={{ duration: 1, ease: 'easeOut' }}
            className={cn(
              "h-full rounded-full",
              isBullish ? "bg-emerald-500" : isBearish ? "bg-rose-500" : "bg-zinc-500"
            )}
          />
        </div>

        <p className="text-xs text-zinc-400 leading-relaxed whitespace-pre-line">
          {result.reasoning}
        </p>

        <div className="mt-4 flex items-center gap-4 text-[10px] text-zinc-500">
          <span className="flex items-center gap-1">
            <Clock size={10} />
            {new Date(result.timestamp).toLocaleTimeString()}
          </span>
          <span className="flex items-center gap-1">
            <Users size={10} />
            {result.agent_opinions.length} agents
          </span>
        </div>
      </div>
    </motion.div>
  );
};

const KronosCard: React.FC<{ kronos: OpinionResult['kronos'] }> = ({ kronos }) => {
  if (!kronos || !kronos.signal) return null;
  const change = kronos.predicted_change_pct || 0;
  const isUp = change > 0;

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="p-4 rounded-xl border border-violet-500/20 bg-violet-500/5"
    >
      <div className="flex items-center gap-2 mb-3">
        <Sparkles size={16} className="text-violet-400" />
        <p className="text-xs font-bold text-violet-300">Kronos Forecast</p>
      </div>
      <div className="flex items-center justify-between">
        <div>
          <p className={cn("text-lg font-black", isUp ? "text-emerald-400" : "text-rose-400")}>
            {change > 0 ? '+' : ''}{change.toFixed(2)}%
          </p>
          <p className="text-[10px] text-zinc-500">
            Predicted close: {kronos.predicted_close?.toFixed(2) || 'N/A'}
          </p>
        </div>
        <div className="text-right">
          <p className="text-[10px] text-zinc-500 uppercase tracking-wider">Confidence</p>
          <p className="text-sm font-bold text-violet-300">
            {Math.round((kronos.confidence || 0) * 100)}%
          </p>
        </div>
      </div>
    </motion.div>
  );
};

const SocialCard: React.FC<{ social: OpinionResult['social'] }> = ({ social }) => {
  if (!social || social.article_count === undefined) return null;
  const score = social.sentiment_score || 0;

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="p-4 rounded-xl border border-sky-500/20 bg-sky-500/5"
    >
      <div className="flex items-center gap-2 mb-3">
        <Globe size={16} className="text-sky-400" />
        <p className="text-xs font-bold text-sky-300">Social Sentiment</p>
      </div>
      <div className="flex items-center justify-between">
        <div>
          <p className={cn("text-lg font-black", score > 0 ? "text-emerald-400" : score < 0 ? "text-rose-400" : "text-zinc-400")}>
            {score > 0 ? '+' : ''}{score.toFixed(3)}
          </p>
          <p className="text-[10px] text-zinc-500">
            {social.article_count} posts analyzed
          </p>
        </div>
        <div className={cn(
          "px-2 py-1 rounded-md text-[10px] font-black uppercase",
          social.direction === 'BUY' ? SIGNAL_COLORS.bullish :
          social.direction === 'SELL' ? SIGNAL_COLORS.bearish :
          SIGNAL_COLORS.neutral
        )}>
          {social.direction || 'NEUTRAL'}
        </div>
      </div>
    </motion.div>
  );
};

const AlertsCard: React.FC<{ alerts: OpinionResult['alerts'] }> = ({ alerts }) => {
  if (!alerts || alerts.length === 0) return null;

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="p-4 rounded-xl border border-amber-500/20 bg-amber-500/5"
    >
      <div className="flex items-center gap-2 mb-3">
        <AlertTriangle size={16} className="text-amber-400" />
        <p className="text-xs font-bold text-amber-300">Market Alerts</p>
      </div>
      <div className="space-y-1.5">
        {alerts.slice(0, 5).map((a, i) => (
          <div key={i} className="flex items-center justify-between text-[11px]">
            <span className="text-zinc-400 capitalize">{a.alert_type}</span>
            <span className={cn(
              "font-mono font-bold",
              a.score > 0 ? "text-emerald-400" : "text-rose-400"
            )}>
              {a.score > 0 ? '+' : ''}{a.score}
            </span>
          </div>
        ))}
      </div>
    </motion.div>
  );
};

export const OpinionLayerView: React.FC = () => {
  const [symbol, setSymbol] = useState('BTCUSDT');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<OpinionResult | null>(null);
  const [error, setError] = useState('');

  const runAnalysis = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      // Fetch bars from market data service
      const bars = await apiService.get(`/api/market-data/bars?symbol=${symbol}&timeframe=1h&limit=100`);
      const res = await apiService.post('/trading/opinion/analyze', {
        symbol,
        bars: bars.data || bars,
        include_kronos: true,
        include_social: true,
        include_alerts: true,
        include_personas: true,
      });
      setResult(res);
    } catch (e: any) {
      setError(e.message || 'Analysis failed');
    } finally {
      setLoading(false);
    }
  }, [symbol]);

  const coreAgents = useMemo(() =>
    result?.agent_opinions.filter(o =>
      ['technical_analyst', 'kronos_foundation', 'social_sentiment', 'market_alerts'].includes(o.agent)
    ) || [],
    [result]
  );

  const personaAgents = useMemo(() =>
    result?.agent_opinions.filter(o =>
      !['technical_analyst', 'kronos_foundation', 'social_sentiment', 'market_alerts'].includes(o.agent)
    ) || [],
    [result]
  );

  return (
    <div className="h-full overflow-y-auto p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-black text-white tracking-tight flex items-center gap-2">
            <BrainCircuit size={22} className="text-violet-400" />
            Opinion Layer
          </h1>
          <p className="text-xs text-zinc-500 mt-1">
            Multi-agent consensus engine — Technical + Kronos + Social + Personas
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2 text-xs font-mono text-white focus:outline-none focus:border-violet-500/50 w-28"
            placeholder="SYMBOL"
          />
          <button
            onClick={runAnalysis}
            disabled={loading}
            className={cn(
              "flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all",
              loading
                ? "bg-zinc-800 text-zinc-500 cursor-not-allowed"
                : "bg-violet-500 hover:bg-violet-400 text-white shadow-lg shadow-violet-500/20"
            )}
          >
            {loading ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
            {loading ? 'Analyzing...' : 'Run Analysis'}
          </button>
        </div>
      </div>

      {/* Error */}
      <AnimatePresence>
        {error && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="p-3 rounded-lg bg-rose-500/10 border border-rose-500/20 text-rose-400 text-xs"
          >
            {error}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Results */}
      <AnimatePresence mode="wait">
        {result && !loading && (
          <motion.div
            key={result.timestamp}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="space-y-6"
          >
            {/* Final Decision */}
            <FinalDecisionCard result={result} />

            {/* Supplementary Cards */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <KronosCard kronos={result.kronos} />
              <SocialCard social={result.social} />
              <AlertsCard alerts={result.alerts} />
            </div>

            {/* Core Agents */}
            <div>
              <h3 className="text-xs font-black text-zinc-400 uppercase tracking-widest mb-3 flex items-center gap-2">
                <Activity size={14} />
                Core Agents
              </h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                {coreAgents.map((op, i) => (
                  <AgentCard key={op.agent} opinion={op} index={i} />
                ))}
              </div>
            </div>

            {/* Persona Agents */}
            {personaAgents.length > 0 && (
              <div>
                <h3 className="text-xs font-black text-zinc-400 uppercase tracking-widest mb-3 flex items-center gap-2">
                  <Users size={14} />
                  Investor Personas
                </h3>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                  {personaAgents.map((op, i) => (
                    <AgentCard key={op.agent} opinion={op} index={i} />
                  ))}
                </div>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Empty State */}
      {!result && !loading && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="flex flex-col items-center justify-center py-20 text-zinc-600"
        >
          <BrainCircuit size={48} className="mb-4 opacity-30" />
          <p className="text-sm font-bold">No analysis yet</p>
          <p className="text-xs mt-1">Enter a symbol and click Run Analysis to see multi-agent consensus</p>
        </motion.div>
      )}
    </div>
  );
};
