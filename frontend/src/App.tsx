/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */
import React, { useState, useCallback, useMemo, useEffect, useRef } from 'react';
import {
  TrendingUp,
  Search,
  Bell,
  LayoutDashboard,
  PieChart,
  Wallet,
  Settings,
  Zap,
  Activity,
  Globe,
  Clock,
  Cpu,
  Plus,
  Play,
  Pause,
  Save,
  Trash2,
  ChevronRight,
  Layers,
  MousePointer2,
  Sparkles,
  MessageSquare,
  Send,
  BrainCircuit,
  X,
  Database,
  BarChart3,
  Cloud,
  Shield,
  Check,
  History,
  LineChart as LineChartIcon,
  RefreshCw,
  Newspaper,
  FlaskConical
} from 'lucide-react';
import {
  AreaChart,
  Area,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer
} from 'recharts';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  addEdge,
  Panel,
  Handle,
  Position,
  NodeProps,
  Edge,
  Node as FlowNode
} from '@xyflow/react';
import { cn } from './lib/utils';
import { motion, AnimatePresence } from 'motion/react';
import { geminiService } from './services/geminiService';
import { configService } from './services/configService';
import { SettingsView } from './components/SettingsView';
import { PortfolioView } from './components/PortfolioView';
import { WalletView } from './components/WalletView';
import { MarketsView } from './components/MarketsView';
import { SignalsView } from './components/SignalsView';
import { StatusView } from './components/StatusView';
import PaperTradingView from './components/PaperTradingView';
import { TradingChart } from './components/TradingChart';
import { OpinionLayerView } from './components/OpinionLayerView';
import { marketDataService, MarketProvider } from './services/marketDataService';
import { brokerService, OrderParams } from './services/brokerService';
import { backtestService, BacktestResult } from './services/backtestService';
import { apiService } from './services/apiService';
import { workflowEngine } from './services/workflowEngine';
import { FilterNode, PositionSizerNode, RiskManagementNode, KillswitchNode } from './components/WorkflowNodes';

// --- Types ---
type AppMode = 'manual' | 'ai' | 'backtest' | 'settings' | 'markets' | 'portfolio' | 'wallet' | 'signals' | 'status' | 'opinion';

interface WorkflowNodeData {
  label: string;
  type: string;
  icon: any;
  color?: string;
  config?: {
    symbol: string;
    quantity: number;
    orderType: 'market' | 'limit';
    side: 'buy' | 'sell';
    broker: 'binance' | 'ctrader';
    price?: number;
  };
}

// --- Mock Data ---
const generateCandleData = () => {
  const data = [];
  let price = 64250;
  const now = new Date();
  // Start 100 minutes ago to ensure mock data ends before current real-time data
  const startTime = new Date(now.getTime() - 100 * 60000);

  for (let i = 0; i < 100; i++) {
    const open = price;
    const close = price + (Math.random() - 0.5) * 200;
    const high = Math.max(open, close) + Math.random() * 50;
    const low = Math.min(open, close) - Math.random() * 50;

    const time = new Date(startTime.getTime() + i * 60000);

    data.push({
      time: Math.floor(time.getTime() / 1000),
      open: Math.round(open * 100) / 100,
      high: Math.round(high * 100) / 100,
      low: Math.round(low * 100) / 100,
      close: Math.round(close * 100) / 100,
      volume: Math.round(Math.random() * 1000 + 500),
    });
    price = close;
  }
  return data;
};

const candleData = generateCandleData();

// --- AI Builder Components ---

const CustomNode = ({ data, selected }: NodeProps) => {
  const nodeData = data as unknown as WorkflowNodeData;
  const Icon = nodeData.icon as any;
  const isConfigured = !!(nodeData.config && ((nodeData.config as any).symbol || (nodeData.config as any).rsiPeriod));

  // Map category to accent colors
  const accentColor =
    nodeData.type === 'Trigger' ? "blue" :
      nodeData.type === 'Condition' ? "amber" :
        nodeData.type === 'Action' ? "emerald" :
          nodeData.type === 'Integration' ? "indigo" : "zinc";

  return (
    <motion.div
      initial={{ scale: 0.9, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      whileHover={{ y: -2, scale: 1.02 }}
      className={cn(
        "px-4 py-3 rounded-2xl bg-zinc-900/40 backdrop-blur-xl border transition-all min-w-[200px] relative group",
        selected
          ? `border-${accentColor}-500/50 shadow-[0_0_25px_rgba(var(--${accentColor}-500-rgb),0.2)]`
          : "border-zinc-800/50 hover:border-zinc-700 shadow-xl"
      )}
      style={{
        boxShadow: selected ? `0 0 20px -5px var(--tw-shadow-color)` : undefined,
      }}
    >
      <Handle type="target" position={Position.Top} className="w-1.5 h-1.5 bg-zinc-700 border-none !-top-1" />

      <div className="flex items-center gap-3">
        <div className={cn(
          "w-10 h-10 rounded-xl flex items-center justify-center relative overflow-hidden",
          nodeData.color || "bg-zinc-800"
        )}>
          {/* Subtle icon background glow */}
          <div className="absolute inset-0 opacity-20 bg-white" />
          {Icon ? <Icon size={20} className="relative z-10 text-white" /> : <Activity size={20} className="relative z-10 text-white" />}

          {/* Status pulse */}
          <div className="absolute top-1 right-1">
            <div className={cn(
              "w-1.5 h-1.5 rounded-full animate-pulse",
              accentColor === 'emerald' ? "bg-emerald-400" :
                accentColor === 'blue' ? "bg-blue-400" :
                  accentColor === 'amber' ? "bg-amber-400" : "bg-zinc-400"
            )} />
          </div>
        </div>

        <div className="flex-1 min-w-0">
          <p className="text-[9px] uppercase text-zinc-500 font-black tracking-[0.1em] leading-none mb-1.5">{nodeData.type}</p>
          <p className="text-xs font-bold text-white leading-none truncate pr-4">{nodeData.label}</p>
        </div>
      </div>

      {isConfigured && (
        <div className="mt-3 pt-3 border-t border-zinc-800/30 flex flex-col gap-1.5">
          <div className="flex items-center justify-between text-[8px] font-mono font-medium tracking-tight text-zinc-500">
            <span className="flex items-center gap-1">
              <Globe size={10} className="opacity-50" /> {nodeData.config?.symbol || 'GLOBAL'}
            </span>
            <span className="bg-zinc-800/50 px-1.5 py-0.5 rounded uppercase">Configured</span>
          </div>
        </div>
      )}

      {selected && (
        <motion.div
          initial={{ scale: 0, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          className="absolute -top-1.5 -right-1.5 w-5 h-5 bg-emerald-500 rounded-full flex items-center justify-center shadow-[0_0_10px_rgba(16,185,129,0.5)] z-20"
        >
          <Check size={12} className="text-black font-bold" />
        </motion.div>
      )}

      <Handle type="source" position={Position.Bottom} className="w-1.5 h-1.5 bg-zinc-700 border-none !-bottom-1" />
    </motion.div>
  );
};

const NodeProperties = ({ node, onUpdate }: { node: FlowNode, onUpdate: (id: string, config: any) => void }) => {
  const nodeData = node.data as unknown as WorkflowNodeData;
  const label = nodeData.label.toLowerCase();

  const [config, setConfig] = useState(nodeData.config || {});

  useEffect(() => {
    setConfig(nodeData.config || {});
  }, [node.id]);

  const handleChange = (field: string, value: any) => {
    const newConfig = { ...config, [field]: value };
    setConfig(newConfig);
    onUpdate(node.id, newConfig);
  };

  const renderConfigFields = () => {
    if (label.includes('rsi')) {
      return (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-[9px] uppercase text-zinc-500 font-bold tracking-wider">Period</label>
              <input type="number" value={config.rsiPeriod} onChange={(e) => handleChange('rsiPeriod', parseInt(e.target.value))} className="w-full bg-zinc-900 border border-zinc-800 rounded-lg py-2 px-3 text-xs font-mono text-white focus:outline-none focus:border-emerald-500/50" />
            </div>
            <div className="space-y-1.5">
              <label className="text-[9px] uppercase text-zinc-500 font-bold tracking-wider">Symbol</label>
              <input type="text" value={config.symbol} onChange={(e) => handleChange('symbol', e.target.value.toUpperCase())} className="w-full bg-zinc-900 border border-zinc-800 rounded-lg py-2 px-3 text-xs font-mono text-white focus:outline-none focus:border-emerald-500/50" />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-[9px] uppercase text-zinc-500 font-bold tracking-wider">Overbought</label>
              <input type="number" value={config.rsiUpper} onChange={(e) => handleChange('rsiUpper', parseInt(e.target.value))} className="w-full bg-zinc-900 border border-zinc-800 rounded-lg py-2 px-3 text-xs font-mono text-white focus:outline-none focus:border-emerald-500/50" />
            </div>
            <div className="space-y-1.5">
              <label className="text-[9px] uppercase text-zinc-500 font-bold tracking-wider">Oversold</label>
              <input type="number" value={config.rsiLower} onChange={(e) => handleChange('rsiLower', parseInt(e.target.value))} className="w-full bg-zinc-900 border border-zinc-800 rounded-lg py-2 px-3 text-xs font-mono text-white focus:outline-none focus:border-emerald-500/50" />
            </div>
          </div>
        </div>
      );
    }

    if (label.includes('trend') || label.includes('ema')) {
      return (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-[9px] uppercase text-zinc-500 font-bold tracking-wider">EMA Fast</label>
              <input type="number" value={config.emaFast} onChange={(e) => handleChange('emaFast', parseInt(e.target.value))} className="w-full bg-zinc-900 border border-zinc-800 rounded-lg py-2 px-3 text-xs font-mono text-white focus:outline-none focus:border-emerald-500/50" />
            </div>
            <div className="space-y-1.5">
              <label className="text-[9px] uppercase text-zinc-500 font-bold tracking-wider">EMA Slow</label>
              <input type="number" value={config.emaSlow} onChange={(e) => handleChange('emaSlow', parseInt(e.target.value))} className="w-full bg-zinc-900 border border-zinc-800 rounded-lg py-2 px-3 text-xs font-mono text-white focus:outline-none focus:border-emerald-500/50" />
            </div>
          </div>
        </div>
      );
    }

    if (label.includes('telegram') || label.includes('discord')) {
      return (
        <div className="space-y-3">
          <div className="space-y-1.5">
            <label className="text-[9px] uppercase text-zinc-500 font-bold tracking-wider">{label.includes('telegram') ? 'Chat ID' : 'Webhook URL'}</label>
            <input
              type="text"
              value={config.target || ''}
              onChange={(e) => handleChange('target', e.target.value)}
              placeholder={label.includes('telegram') ? '@my_chat_id' : 'https://discord.com/api/webhooks/...'}
              className="w-full bg-zinc-900 border border-zinc-800 rounded-lg py-2 px-3 text-xs font-mono text-white focus:outline-none focus:border-emerald-500/50"
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-[9px] uppercase text-zinc-500 font-bold tracking-wider">Message Template</label>
            <textarea
              value={config.message || ''}
              onChange={(e) => handleChange('message', e.target.value)}
              placeholder="Signal alert: {{symbol}} {{side}} at {{price}}"
              className="w-full h-24 bg-zinc-900 border border-zinc-800 rounded-lg py-2 px-3 text-xs font-mono text-white focus:outline-none focus:border-emerald-500/50 resize-none"
            />
          </div>
        </div>
      );
    }

    if (label.includes('sql') || label.includes('postgres') || label.includes('mysql')) {
      return (
        <div className="space-y-3">
          <div className="space-y-1.5">
            <label className="text-[9px] uppercase text-zinc-500 font-bold tracking-wider">SQL Query</label>
            <textarea
              value={config.query || ''}
              onChange={(e) => handleChange('query', e.target.value)}
              placeholder="INSERT INTO trades (symbol, price) VALUES ('BTCUSDT', 65000);"
              className="w-full h-32 bg-zinc-900 border border-zinc-800 rounded-lg py-2 px-3 text-xs font-mono text-white focus:outline-none focus:border-emerald-500/50 resize-none"
            />
          </div>
        </div>
      );
    }

    if (label.includes('delay')) {
      return (
        <div className="space-y-3">
          <div className="space-y-1.5">
            <label className="text-[9px] uppercase text-zinc-500 font-bold tracking-wider">Duration (ms)</label>
            <input type="number" value={config.delayMs} onChange={(e) => handleChange('delayMs', parseInt(e.target.value))} className="w-full bg-zinc-900 border border-zinc-800 rounded-lg py-2 px-3 text-xs font-mono text-white focus:outline-none focus:border-emerald-500/50" />
          </div>
        </div>
      );
    }

    if (label.includes('switch') || label.includes('filter')) {
      return (
        <div className="space-y-3">
          <div className="space-y-1.5">
            <label className="text-[9px] uppercase text-zinc-500 font-bold tracking-wider">Condition Expression (JS)</label>
            <textarea
              value={config.expression || ''}
              onChange={(e) => handleChange('expression', e.target.value)}
              placeholder="data.price > 60000 && data.rsi < 30"
              className="w-full h-24 bg-zinc-900 border border-zinc-800 rounded-lg py-2 px-3 text-xs font-mono text-white focus:outline-none focus:border-emerald-500/50 resize-none"
            />
          </div>
        </div>
      );
    }

    if (label.includes('buy') || label.includes('sell') || nodeData.type === 'Integration' || nodeData.type === 'Action') {
      return (
        <div className="space-y-3">
          <div className="space-y-1.5">
            <label className="text-[9px] uppercase text-zinc-500 font-bold tracking-wider">Symbol</label>
            <input type="text" value={config.symbol} onChange={(e) => handleChange('symbol', e.target.value.toUpperCase())} className="w-full bg-zinc-900 border border-zinc-800 rounded-lg py-2 px-3 text-xs font-mono text-white focus:outline-none focus:border-emerald-500/50" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-[9px] uppercase text-zinc-500 font-bold tracking-wider">Quantity</label>
              <input type="number" value={config.quantity} onChange={(e) => handleChange('quantity', parseFloat(e.target.value))} className="w-full bg-zinc-900 border border-zinc-800 rounded-lg py-2 px-3 text-xs font-mono text-white focus:outline-none focus:border-emerald-500/50" />
            </div>
            <div className="space-y-1.5">
              <label className="text-[9px] uppercase text-zinc-500 font-bold tracking-wider">Side</label>
              <select value={config.side} onChange={(e) => handleChange('side', e.target.value)} className="w-full bg-zinc-900 border border-zinc-800 rounded-lg py-2 px-3 text-xs font-mono text-white focus:outline-none focus:border-emerald-500/50">
                <option value="buy">Buy</option>
                <option value="sell">Sell</option>
              </select>
            </div>
          </div>
          <div className="space-y-1.5">
            <label className="text-[9px] uppercase text-zinc-500 font-bold tracking-wider">Order Type</label>
            <div className="flex bg-zinc-900 rounded-lg p-1 border border-zinc-800">
              {['market', 'limit'].map(t => (
                <button key={t} onClick={() => handleChange('orderType', t)} className={cn("flex-1 py-1.5 text-[10px] font-bold rounded-md transition-all uppercase tracking-tighter", config.orderType === t ? "bg-zinc-800 text-white shadow-sm" : "text-zinc-500 hover:text-zinc-400")}>{t}</button>
              ))}
            </div>
          </div>
          {config.orderType === 'limit' && (
            <div className="space-y-1.5">
              <label className="text-[9px] uppercase text-zinc-500 font-bold tracking-wider">Limit Price</label>
              <input type="number" value={config.price || ''} onChange={(e) => handleChange('price', parseFloat(e.target.value))} className="w-full bg-zinc-900 border border-zinc-800 rounded-lg py-2 px-3 text-xs font-mono text-white focus:outline-none focus:border-emerald-500/50" placeholder="0.00" />
            </div>
          )}
        </div>
      );
    }

    return (
      <div className="p-4 bg-zinc-900/50 border border-dashed border-zinc-800 rounded-lg text-center">
        <p className="text-[10px] text-zinc-500">No specific configuration available for this node type.</p>
      </div>
    );
  };

  return (
    <div className="bg-[#141416] border border-zinc-800 rounded-xl p-4 shadow-2xl min-w-[280px] space-y-4">
      <div className="flex items-center gap-2 border-b border-zinc-800 pb-3">
        <div className={cn("p-1.5 rounded-lg", node.data.color)}>
          {React.createElement(node.data.icon as any, { size: 14, className: "text-white" })}
        </div>
        <div>
          <h3 className="text-xs font-bold text-white">{node.data.label}</h3>
          <p className="text-[9px] text-zinc-500 uppercase font-bold tracking-widest">{nodeData.type} Configuration</p>
        </div>
      </div>
      {renderConfigFields()}
    </div>
  );
};

const nodeTypes = {
  workflow: CustomNode,
  filter: FilterNode,
  positionSizer: PositionSizerNode,
  riskManagement: RiskManagementNode,
  killswitch: KillswitchNode,
};

const initialNodes: FlowNode[] = [
  {
    id: '1',
    type: 'workflow',
    position: { x: 250, y: 50 },
    data: {
      label: 'RSI Indicator',
      type: 'Trigger',
      icon: Activity,
      color: 'bg-blue-500/20',
      config: { symbol: 'BTCUSDT', rsiPeriod: 14, rsiLower: 30, rsiUpper: 70 }
    }
  },
  {
    id: '2',
    type: 'workflow',
    position: { x: 250, y: 200 },
    data: {
      label: 'Trend Check',
      type: 'Condition',
      icon: TrendingUp,
      color: 'bg-amber-500/20',
      config: { emaFast: 20, emaSlow: 50 }
    }
  },
  {
    id: '3',
    type: 'workflow',
    position: { x: 250, y: 350 },
    data: {
      label: 'Execute Buy',
      type: 'Action',
      icon: Zap,
      color: 'bg-emerald-500/20',
      config: { symbol: 'BTCUSDT', quantity: 0.05, orderType: 'market', side: 'buy' }
    }
  },
  {
    id: '4',
    type: 'workflow',
    position: { x: 500, y: 200 },
    data: {
      label: 'Binance API',
      type: 'Integration',
      icon: Zap,
      color: 'bg-indigo-500/20',
      config: { symbol: 'BTCUSDT', quantity: 0.01, orderType: 'market', side: 'buy', broker: 'binance' }
    }
  },
];

const initialEdges: Edge[] = [
  { id: 'e1-2', source: '1', target: '2', animated: true, style: { stroke: '#10b981' } },
  { id: 'e2-3', source: '2', target: '3', animated: true, style: { stroke: '#10b981' } },
];

// --- Sub-Components ---

const OrderBookRow: React.FC<{ price: string, amount: string, total: string, type: 'bid' | 'ask', depth: number }> = ({ price, amount, total, type, depth }) => (
  <div className="relative grid grid-cols-3 py-1 px-4 hover:bg-white/5 cursor-pointer text-[12px] font-mono group overflow-hidden">
    <div
      className={cn(
        "absolute right-0 top-0 bottom-0 opacity-10 transition-all duration-500",
        type === 'bid' ? "bg-emerald-500" : "bg-rose-500"
      )}
      style={{ width: `${depth}%` }}
    />
    <span className={cn("z-10", type === 'bid' ? "text-emerald-400" : "text-rose-400")}>{price}</span>
    <span className="text-zinc-400 text-right z-10">{amount}</span>
    <span className="text-zinc-500 text-right z-10">{total}</span>
  </div>
);

const SymbolSearch = ({ currentSymbol, currentProvider, onSelect }: { currentSymbol: string, currentProvider: MarketProvider, onSelect: (symbol: string, provider: MarketProvider) => void }) => {
  const [query, setQuery] = useState(currentSymbol);
  const [results, setResults] = useState<any[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  const [isSearching, setIsSearching] = useState(false);
  const searchRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (searchRef.current && !searchRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  useEffect(() => {
    const delayDebounceFn = setTimeout(async () => {
      if (query.length >= 2 && isOpen) {
        setIsSearching(true);
        const searchResults = await marketDataService.searchSymbols(query);
        setResults(searchResults);
        setIsSearching(false);
      } else {
        setResults([]);
      }
    }, 500);

    return () => clearTimeout(delayDebounceFn);
  }, [query, isOpen]);

  return (
    <div className="relative" ref={searchRef}>
      <div className="flex items-center gap-2 px-3 py-1.5 bg-zinc-900 rounded-xl border border-zinc-800 focus-within:border-emerald-500/50 transition-all">
        <div className={cn(
          "w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold text-black shrink-0",
          currentProvider === 'binance' ? "bg-orange-500" : "bg-blue-500"
        )}>
          {currentProvider === 'binance' ? '₿' : 'S'}
        </div>
        <input
          type="text"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value.toUpperCase());
            setIsOpen(true);
          }}
          onFocus={() => setIsOpen(true)}
          placeholder="Search symbol..."
          className="bg-transparent border-none text-sm font-bold w-24 focus:outline-none placeholder:text-zinc-600"
        />
        {isSearching ? (
          <div className="w-3 h-3 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin" />
        ) : (
          <div className="w-1.5 h-1.5 bg-emerald-500 rounded-full animate-pulse" />
        )}
        <Search size={14} className="text-zinc-500 ml-1" />
      </div>

      <AnimatePresence>
        {isOpen && results.length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 10 }}
            className="absolute top-full left-0 right-0 mt-2 bg-[#1c1c1f] border border-zinc-800 rounded-xl shadow-2xl z-50 max-h-[300px] overflow-y-auto scrollbar-hide"
          >
            {results.map((res, i) => (
              <div
                key={i}
                onClick={() => {
                  onSelect(res.symbol, currentProvider);
                  setQuery(res.symbol);
                  setIsOpen(false);
                }}
                className="p-3 hover:bg-white/5 cursor-pointer border-b border-zinc-800/50 last:border-none flex flex-col gap-0.5"
              >
                <div className="flex items-center justify-between">
                  <span className="font-bold text-xs text-emerald-400">{res.symbol}</span>
                  <span className="text-[8px] text-zinc-500 uppercase tracking-widest">{res.type || 'Asset'}</span>
                </div>
                <span className="text-[10px] text-zinc-400 truncate">{res.name}</span>
                {res.region && <span className="text-[8px] text-zinc-600">{res.region}</span>}
              </div>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};

const NavItem = ({ icon, label, active = false, badge, onClick }: { icon: React.ReactNode, label: string, active?: boolean, badge?: string, onClick?: () => void }) => (
  <div
    onClick={onClick}
    className={cn(
      "flex items-center gap-3 p-3 rounded-xl cursor-pointer transition-all group",
      active ? "bg-emerald-500/10 text-emerald-400" : "text-zinc-500 hover:bg-white/5 hover:text-zinc-300"
    )}
  >
    <div className={cn("transition-transform group-hover:scale-110", active && "text-emerald-400")}>
      {icon}
    </div>
    <span className="hidden lg:block text-sm font-medium">{label}</span>
    {badge && <span className="hidden lg:block ml-auto px-1.5 py-0.5 text-[8px] font-bold rounded bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">{badge}</span>}
    {active && !badge && <div className="hidden lg:block ml-auto w-1.5 h-1.5 bg-emerald-400 rounded-full shadow-[0_0_8px_rgba(52,211,153,0.6)]" />}
  </div>
);

const PositionRow = ({ asset, type, entry, mark, pnl, roe, isPositive, onClose }: any) => (
  <tr className="border-b border-zinc-800/50 hover:bg-white/5 transition-colors group">
    <td className="px-6 py-4 font-semibold">{asset}</td>
    <td className="px-6 py-4">
      <span className={cn(
        "px-2 py-0.5 rounded text-[10px] font-bold uppercase",
        type === 'Long' ? "bg-emerald-500/10 text-emerald-400" : "bg-rose-500/10 text-rose-400"
      )}>
        {type}
      </span>
    </td>
    <td className="px-6 py-4 text-zinc-400">{entry}</td>
    <td className="px-6 py-4 text-zinc-400">{mark}</td>
    <td className="px-6 py-4">
      <div className={cn("flex flex-col", isPositive ? "text-emerald-400" : "text-rose-400")}>
        <span>{pnl}</span>
        <span className="text-[10px] opacity-70">{roe}</span>
      </div>
    </td>
    <td className="px-6 py-4 text-right">
      <button onClick={onClose} className="text-xs bg-zinc-800 hover:bg-zinc-700 px-3 py-1.5 rounded-lg transition-colors">
        Close
      </button>
    </td>
  </tr>
);

// --- Gemini Chat Component ---
interface Message {
  role: 'user' | 'model';
  text: string;
}

interface GeminiChatProps {
  isOpen: boolean;
  onClose: () => void;
  messages: Message[];
  onSendMessage: (text: string) => void;
  isLoading: boolean;
}

const GeminiChat = ({ isOpen, onClose, messages, onSendMessage, isLoading }: GeminiChatProps) => {
  const [input, setInput] = useState('');

  const handleSend = () => {
    if (!input.trim() || isLoading) return;
    onSendMessage(input);
    setInput('');
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          initial={{ opacity: 0, y: 20, scale: 0.95 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: 20, scale: 0.95 }}
          className="fixed bottom-6 right-6 w-96 h-[500px] bg-[#141416] border border-zinc-800 rounded-2xl shadow-2xl flex flex-col z-50 overflow-hidden"
        >
          <div className="p-4 border-b border-zinc-800 flex items-center justify-between bg-emerald-500/5">
            <div className="flex items-center gap-2">
              <div className="p-1.5 bg-emerald-500/20 rounded-lg">
                <Sparkles size={16} className="text-emerald-400" />
              </div>
              <span className="font-bold text-sm">Gemini Assistant</span>
            </div>
            <button onClick={onClose} className="text-zinc-500 hover:text-white transition-colors">
              <X size={18} />
            </button>
          </div>

          <div className="flex-1 overflow-y-auto p-4 space-y-4 scrollbar-hide">
            {messages.length === 0 && (
              <div className="h-full flex flex-col items-center justify-center text-center p-6 space-y-2">
                <BrainCircuit size={40} className="text-zinc-700" />
                <p className="text-sm text-zinc-500 font-medium">How can I help with your trading today?</p>
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={cn("flex", m.role === 'user' ? "justify-end" : "justify-start")}>
                <div className={cn(
                  "max-w-[85%] p-3 rounded-2xl text-xs leading-relaxed",
                  m.role === 'user' ? "bg-emerald-500 text-black font-medium" : "bg-zinc-900 border border-zinc-800 text-zinc-300"
                )}>
                  {m.text}
                </div>
              </div>
            ))}
            {isLoading && (
              <div className="flex justify-start">
                <div className="bg-zinc-900 border border-zinc-800 p-3 rounded-2xl flex gap-1">
                  <div className="w-1 h-1 bg-zinc-500 rounded-full animate-bounce" />
                  <div className="w-1 h-1 bg-zinc-500 rounded-full animate-bounce [animation-delay:0.2s]" />
                  <div className="w-1 h-1 bg-zinc-500 rounded-full animate-bounce [animation-delay:0.4s]" />
                </div>
              </div>
            )}
          </div>

          <div className="p-4 border-t border-zinc-800 bg-zinc-900/30">
            <div className="relative">
              <input
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSend()}
                placeholder="Ask Gemini..."
                className="w-full bg-zinc-900 border border-zinc-800 rounded-xl py-2.5 pl-4 pr-12 text-xs focus:outline-none focus:border-emerald-500/50"
              />
              <button
                onClick={handleSend}
                disabled={isLoading}
                className="absolute right-2 top-1/2 -translate-y-1/2 p-1.5 bg-emerald-500 text-black rounded-lg hover:bg-emerald-400 transition-colors disabled:opacity-50"
              >
                <Send size={14} />
              </button>
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
};

// --- Main App ---
import { useToast } from './components/Toast';
import { MiniCandleChart } from './components/MiniCandleChart';
import { ActiveTradeCard } from './components/ActiveTradeCard';
import type { ActiveTrade } from './components/ActiveTradeCard';
import { NewsDataPanel } from './components/NewsDataPanel';
import { newsDataService } from './services/newsDataService';

export default function App() {
  const { showToast } = useToast();
  const [mode, setMode] = useState<AppMode>('manual');
  const [backendConnected, setBackendConnected] = useState<boolean | null>(null);
  const [loopRunning, setLoopRunning] = useState(false);
  const [dryRun, setDryRun] = useState(true);
  const [activeTab, setActiveTab] = useState('buy');
  const [orderType, setOrderType] = useState('limit');
  const [price, setPrice] = useState('64,250.40');
  const [timeframe, setTimeframe] = useState('1h');
  const [manualAmount, setManualAmount] = useState('0.1');

  const parsePrice = (p: string) => {
    return parseFloat(p.toString().replace(/,/g, ''));
  };

  const [isChatOpen, setIsChatOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [isAiLoading, setIsAiLoading] = useState(false);
  const [isNotificationsOpen, setIsNotificationsOpen] = useState(false);

  const [aiInsight, setAiInsight] = useState<string | null>(null);
  const [isOptimizing, setIsOptimizing] = useState(false);
  const [isNodeSelectorOpen, setIsNodeSelectorOpen] = useState(false);
  const [nodeSearch, setNodeSearch] = useState('');
  const [isSidebarHidden, setIsSidebarHidden] = useState(false);
  const [newsPanel, setNewsPanel] = useState(false);
  const [isZenMode, setIsZenMode] = useState(false);

  // Backtesting State
  const [backtestResults, setBacktestResults] = useState<BacktestResult | null>(null);
  const [isBacktesting, setIsBacktesting] = useState(false);
  const [backtestProgress, setBacktestProgress] = useState(0);

  // Portfolio & Trading State
  const [openPositions, setOpenPositions] = useState<any[]>([]);
  const [selectedSymbol, setSelectedSymbol] = useState<string>('BTCUSDT');
  const [backendPositions, setBackendPositions] = useState<ActiveTrade[]>([]);
  const [positionsLoading, setPositionsLoading] = useState<boolean>(false);
  const [manualSl, setManualSl] = useState<string>('');
  const [manualTp, setManualTp] = useState<string>('');
  const [manualOrderType, setManualOrderType] = useState<string>('market');
  const [recentTrades, setRecentTrades] = useState<any[]>([]);
  const [portfolioBalance, setPortfolioBalance] = useState(24500.00);

  // Workflow Execution State
  const [isExecutingFlow, setIsExecutingFlow] = useState(false);
  const [executionResults, setExecutionResults] = useState<any>(null);

  const totalPnL = useMemo(() => {
    return openPositions.reduce((acc, pos) => {
      const markPrice = parsePrice(price) || pos.mark;
      const diff = pos.type === 'Long' ? markPrice - pos.entry : pos.entry - markPrice;
      return acc + (diff * pos.amount);
    }, 0);
  }, [openPositions, price]);

  const pnlPercent = (totalPnL / 24500) * 100;

  // Backend Health Check — polls every 30s to update sidebar badges
  useEffect(() => {
    const checkBackend = async () => {
      try {
        const st = await apiService.getStatus();
        setBackendConnected(true);
        setDryRun(st.dry_run);
        const ls = await apiService.getLoopStatus().catch(() => null);
        setLoopRunning(ls?.running ?? false);
      } catch {
        setBackendConnected(false);
        setLoopRunning(false);
      }
    };
    checkBackend();
    const iv = setInterval(checkBackend, 30_000);
    return () => clearInterval(iv);
  }, []);

  // Market Data State
  const [symbol, setSymbol] = useState('BTCUSDT');
  const [provider, setProvider] = useState<MarketProvider>('binance');
  const [candles, setCandles] = useState(candleData);
  const [depth, setDepth] = useState<{ bids: [number, number][], asks: [number, number][] }>({
    bids: [[64248.80, 2.100], [64247.50, 0.540], [64246.20, 1.200], [64245.10, 0.100], [64244.30, 3.450]],
    asks: [[64258.10, 0.124], [64257.40, 1.450], [64256.80, 0.890], [64255.20, 2.100], [64254.90, 0.050]]
  });

  useEffect(() => {
    // Clear state for new symbol
    setCandles([]);
    setDepth({ bids: [], asks: [] });

    // Load API Key from settings
    const savedSettings = localStorage.getItem('quantum_trade_settings');
    if (savedSettings) {
      const settings = JSON.parse(savedSettings);
      if (settings.ALPHAVANTAGE_API_KEY) {
        marketDataService.setApiKey(settings.ALPHAVANTAGE_API_KEY);
      }
    }

    marketDataService.setSymbol(symbol, provider);
    marketDataService.connect();

    const unsubKline = marketDataService.onKline((candle) => {
      setCandles(prev => {
        const last = prev[prev.length - 1];
        if (last && last.time === candle.time) {
          return [...prev.slice(0, -1), candle];
        } else {
          return [...prev, candle].slice(-200);
        }
      });
      setPrice(candle.close.toFixed(2));
    });

    const unsubDepth = marketDataService.onDepth((newDepth) => {
      setDepth(newDepth);
    });

    return () => {
      unsubKline();
      unsubDepth();
      marketDataService.disconnect();
    };
  }, [symbol, provider]);

  // React Flow State
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);
  const [workflows, setWorkflows] = useState<{ id: string; name: string; nodes: FlowNode[]; edges: Edge[]; isRunning: boolean }[]>(() => {
    try {
      const saved = localStorage.getItem('quantum_custom_workflows');
      if (saved) return JSON.parse(saved);
    } catch(e) {}
    return [];
  });

  useEffect(() => {
    localStorage.setItem('quantum_custom_workflows', JSON.stringify(workflows));
  }, [workflows]);
  const [activeWorkflowId, setActiveWorkflowId] = useState<string | null>(null);

  const selectedNodes = useMemo(() => nodes.filter((n) => n.selected), [nodes]);

  const onConnect = useCallback(
    (params: any) => setEdges((eds) => addEdge({ ...params, animated: true, style: { stroke: '#10b981' } }, eds)),
    [setEdges]
  );

  const updateNodeConfig = useCallback((nodeId: string, config: any) => {
    setNodes((nds) => nds.map((node) => {
      if (node.id === nodeId) {
        return { ...node, data: { ...node.data, config } };
      }
      return node;
    }));
  }, [setNodes]);

  const executeNodeTrade = async (node: FlowNode) => {
    const nodeData = node.data as unknown as WorkflowNodeData;
    const config = nodeData.config;
    if (!config || !config.symbol || !config.quantity) {
      setAiInsight('Configuration Error: Please configure the node (symbol, quantity) before executing.');
      showToast('Configuration Error: Please configure the node before executing.', 'error');
      return;
    }

    const broker = config.broker || (nodeData.label.toLowerCase().includes('binance') ? 'binance' : 'ctrader');

    try {
      showToast(`Executing ${config.side.toUpperCase()} order on ${broker.toUpperCase()}...`, 'info');
      const execPrice = config.price || parsePrice(price);
      const result = await brokerService.executeTrade(broker, {
        symbol: config.symbol,
        quantity: config.quantity,
        type: config.orderType,
        side: config.side,
        price: execPrice
      });

      if (result.success) {
        setAiInsight(`Trade Executed Successfully!\nOrder ID: ${result.orderId}\nBroker: ${broker.toUpperCase()}\nSymbol: ${config.symbol}`);
        showToast(`Trade successful! Order ID: ${result.orderId}`, 'success');

        // Update local portfolio state with real data
        const cost = config.quantity * execPrice;
        setPortfolioBalance(prev => prev - (config.side === 'buy' ? cost : -cost));

        setOpenPositions(prev => [...prev, {
          id: result.orderId,
          asset: config.symbol,
          type: config.side === 'buy' ? 'Long' : 'Short',
          entry: execPrice,
          mark: execPrice,
          pnl: 0,
          roe: 0,
          isPositive: true,
          amount: config.quantity
        }]);

        setRecentTrades(prev => [{
          price: execPrice.toFixed(2),
          amount: config.quantity.toFixed(3),
          time: new Date().toLocaleTimeString(),
          type: config.side === 'buy' ? 'bid' : 'ask'
        }, ...prev].slice(0, 10));

      } else {
        setAiInsight(`Trade Execution Failed: ${result.error}`);
        showToast(`Trade Failed: ${result.error}`, 'error');
      }
    } catch (error) {
      setAiInsight(`Trade Execution Error: ${error instanceof Error ? error.message : 'Unknown error'}`);
      showToast(`Trade Error: ${error instanceof Error ? error.message : 'Unknown error'}`, 'error');
    }
  };

  const saveWorkflow = useCallback(() => {
    const name = prompt('Enter workflow name:', activeWorkflowId ? workflows.find(w => w.id === activeWorkflowId)?.name : 'New Workflow');
    if (!name) return;

    const id = activeWorkflowId || `wf-${Date.now()}`;
    const newWorkflow = { id, name, nodes, edges, isRunning: false };

    setWorkflows(prev => {
      const exists = prev.find(w => w.id === id);
      if (exists) {
        return prev.map(w => w.id === id ? newWorkflow : w);
      }
      return [...prev, newWorkflow];
    });
    setActiveWorkflowId(id);
    showToast(`Workflow "${name}" saved completely.`, 'success');
  }, [nodes, edges, activeWorkflowId, workflows]);

  const loadWorkflow = useCallback((id: string) => {
    const wf = workflows.find(w => w.id === id);
    if (wf) {
      setNodes(wf.nodes);
      setEdges(wf.edges);
      setActiveWorkflowId(id);
      showToast(`Loaded workflow: ${wf.name}`, 'info');
    }
  }, [workflows, setNodes, setEdges]);

  const toggleWorkflowRun = useCallback((id: string) => {
    setWorkflows(prev => prev.map(w => {
      if (w.id === id) {
        showToast(w.isRunning ? `Paused workflow: ${w.name}` : `Started workflow: ${w.name}`, w.isRunning ? 'info' : 'success');
        return { ...w, isRunning: !w.isRunning };
      }
      return w;
    }));
  }, []);

  const createNewWorkflow = useCallback(() => {
    setNodes([]);
    setEdges([]);
    setActiveWorkflowId(null);
    showToast('Created new empty workflow.', 'info');
  }, [setNodes, setEdges]);

  const handleBatchDelete = useCallback(() => {
    const selectedIds = new Set(selectedNodes.map((n) => n.id));
    setNodes((nds) => nds.filter((n) => !selectedIds.has(n.id)));
    setEdges((eds) => eds.filter((e) => !selectedIds.has(e.source) && !selectedIds.has(e.target)));
  }, [selectedNodes, setNodes, setEdges]);

  const handleBatchColorChange = useCallback((color: string) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (selectedNodes.find((sn) => sn.id === node.id)) {
          return {
            ...node,
            data: { ...node.data, color },
          };
        }
        return node;
      })
    );
  }, [selectedNodes, setNodes]);

  const handleGroupNodes = useCallback(() => {
    if (selectedNodes.length < 2) return;

    const minX = Math.min(...selectedNodes.map((n) => n.position.x));
    const minY = Math.min(...selectedNodes.map((n) => n.position.y));
    const maxX = Math.max(...selectedNodes.map((n) => n.position.x + 180)); // Approx width
    const maxY = Math.max(...selectedNodes.map((n) => n.position.y + 60));  // Approx height

    const groupId = `group-${Date.now()}`;
    const groupNode: FlowNode = {
      id: groupId,
      type: 'group',
      data: { label: 'New Group' },
      position: { x: minX - 20, y: minY - 40 },
      style: {
        width: maxX - minX + 40,
        height: maxY - minY + 60,
        backgroundColor: 'rgba(16, 185, 129, 0.05)',
        border: '1px dashed #10b981',
        borderRadius: '12px',
      },
    };

    setNodes((nds) => [
      ...nds.map((node) => {
        if (selectedNodes.find((sn) => sn.id === node.id)) {
          return {
            ...node,
            parentId: groupId,
            extent: 'parent' as const,
            position: {
              x: node.position.x - (minX - 20),
              y: node.position.y - (minY - 40),
            },
          };
        }
        return node;
      }),
      groupNode,
    ]);
  }, [selectedNodes, setNodes]);

  const updateNodeData = useCallback((nodeId: string, newData: any) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          return { ...node, data: { ...node.data, ...newData } };
        }
        return node;
      })
    );
  }, [setNodes]);

  const handleRunBacktest = async () => {
    setIsBacktesting(true);
    setBacktestProgress(0);
    try {
      // Simulate progress
      const interval = setInterval(() => {
        setBacktestProgress(prev => Math.min(prev + 10, 90));
      }, 300);

      const results = await backtestService.runBacktest(nodes, edges, symbol, '1h', 500);

      clearInterval(interval);
      setBacktestProgress(100);
      setBacktestResults(results);

      // Get AI analysis
      const analysis = await backtestService.getAiAnalysis(results);
      setAiInsight(analysis.summary);
    } catch (error) {
      console.error('Backtest error:', error);
      setAiInsight('Failed to run backtest. Please check your connection and try again.');
    } finally {
      setIsBacktesting(false);
    }
  };

  const handleChatSend = async (text: string) => {
    if (!text.trim() || isAiLoading) return;

    setMessages(prev => [...prev, { role: 'user', text }]);
    setIsAiLoading(true);

    try {
      const history = messages.map(m => ({
        role: m.role,
        parts: [{ text: m.text }]
      }));
      const response = await geminiService.chat(text, history);
      setMessages(prev => [...prev, { role: 'model', text: response || 'No response' }]);
    } catch (error: any) {
      console.error('Gemini Chat Error:', error);
      const msg = error?.message || 'Could not connect to Gemini.';
      setMessages(prev => [...prev, { role: 'model', text: `Error: ${msg}` }]);
      showToast(`Gemini Chat Error: ${msg}`, 'error');
    } finally {
      setIsAiLoading(false);
    }
  };

  const handleMarketAnalysis = async () => {
    setAiInsight('Analyzing market data...');
    setIsAiLoading(true);
    try {
      const insight = await geminiService.analyzeMarket(candles.slice(-10));
      setAiInsight(insight || 'No insight available.');

      // Also post to chat for history
      setMessages(prev => [...prev,
      { role: 'user', text: `Analyze the current ${symbol} market data.` },
      { role: 'model', text: insight || 'Analysis failed.' }
      ]);
    } catch (error: any) {
      const msg = error?.message || 'Failed to analyze market.';
      setAiInsight(`Error: ${msg}`);
      showToast(`Analysis failed: ${msg}`, 'error');
    } finally {
      setIsAiLoading(false);
    }
  };


  const handleWorkflowOptimization = async () => {
    setIsOptimizing(true);
    setIsAiLoading(true);
    showToast('Optimizing workflow with Gemini...', 'info');

    try {
      const suggestion = await geminiService.optimizeWorkflow({ nodes, edges });

      setMessages(prev => [...prev,
      { role: 'user', text: 'Optimize my current trading workflow and suggest improvements.' },
      { role: 'model', text: suggestion || 'No optimization suggestions found.' }
      ]);

      setIsChatOpen(true);
      showToast('Optimization complete! Check Gemini Chat for details.', 'success');
    } catch (error) {
      console.error('Optimization error:', error);
      showToast('Optimization failed. Please try again.', 'error');
    } finally {
      setIsOptimizing(false);
      setIsAiLoading(false);
    }
  };

  const handleBacktestAnalysis = async () => {
    if (!backtestResults) return;
    setIsAiLoading(true);
    showToast('Gemini is analyzing backtest results...', 'info');

    try {
      const analysis = await geminiService.analyzeBacktest(backtestResults);
      setMessages(prev => [...prev,
      { role: 'user', text: 'Analyze these backtest results and suggest how to improve this strategy.' },
      { role: 'model', text: analysis || 'No analysis available.' }
      ]);
      setIsChatOpen(true);
      showToast('Analysis complete! Check Gemini Chat.', 'success');
    } catch (error) {
      showToast('Analysis failed.', 'error');
    } finally {
      setIsAiLoading(false);
    }
  };



  const addNewNode = useCallback(() => {
    setIsNodeSelectorOpen(true);
  }, []);

  const createNode = useCallback((type: string, label: string, icon: React.ReactNode) => {
    const id = `${type.toLowerCase()}-${Date.now()}`;
    const newNode: FlowNode = {
      id,
      type: 'workflow',
      position: { x: 100 + Math.random() * 300, y: 100 + Math.random() * 300 },
      data: {
        label,
        type,
        icon,
        color: type === 'Trigger' ? 'bg-blue-500/20' :
          type === 'Condition' ? 'bg-amber-500/20' :
            type === 'Action' ? 'bg-emerald-500/20' :
              type === 'Integration' ? 'bg-indigo-500/20' : 'bg-zinc-500/20',
        config: {
          symbol: 'BTCUSDT',
          quantity: 0.01,
          orderType: 'market',
          side: 'buy',
          broker: label.toLowerCase().includes('binance') ? 'binance' : 'ctrader',
          // Default indicator/alert settings
          rsiPeriod: 14,
          rsiUpper: 70,
          rsiLower: 30,
          priceThreshold: 65000,
          delayMs: 1000,
          query: '',
          message: '',
          target: ''
        }
      },
    };
    setNodes((nds) => nds.concat(newNode));
    setIsNodeSelectorOpen(false);
    setNodeSearch('');
  }, [setNodes]);


  // ── Manual Dashboard ───────────────────────────────────────────────────────
  const WATCHLIST = [
    { symbol: 'BTCUSDT', display: 'BTC/USDT' },
    { symbol: 'ETHUSDT', display: 'ETH/USDT' },
    { symbol: 'SOLUSDT', display: 'SOL/USDT' },
    { symbol: 'EURUSD=X', display: 'EUR/USD' },
    { symbol: 'GBPUSD=X', display: 'GBP/USD' },
  ];

  const fetchBackendPositions = async () => {
    setPositionsLoading(true);
    try {
      const data = await apiService.getPositions();
      const converted: ActiveTrade[] = (data.positions || []).map((p: any) => ({
        id: p.id,
        symbol: p.symbol,
        side: (p.direction === 'BUY' ? 'long' : 'short') as 'long' | 'short',
        quantity: p.quantity,
        entryPrice: p.entry_price,
        currentPrice: p.current_price,
        stopLoss: p.stop_loss,
        takeProfit: p.take_profit,
        pnl: p.unrealized_pnl,
        pnlPct: p.unrealized_pnl_pct,
        openedAt: p.opened_at,
        strategy: p.strategy,
      }));
      setBackendPositions(converted);
    } catch (err) {
      console.error('Failed to fetch positions:', err);
    } finally {
      setPositionsLoading(false);
    }
  };

  const localActiveTrades: ActiveTrade[] = openPositions.map((pos) => ({
    id: pos.id || String(Date.now()),
    symbol: pos.asset || symbol,
    side: (pos.type === 'Long' ? 'long' : 'short') as 'long' | 'short',
    quantity: pos.amount || 0,
    entryPrice: pos.entry || 0,
    currentPrice: pos.mark || pos.entry || 0,
    stopLoss: null,
    takeProfit: null,
    pnl: pos.pnl || 0,
    pnlPct: pos.roe || 0,
    openedAt: null,
    strategy: 'Manual',
  }));

  const allPositions: ActiveTrade[] = [
    ...backendPositions,
    ...localActiveTrades.filter(
      (lt) => !backendPositions.some((bp) => String(bp.id) === String(lt.id))
    ),
  ];

  const handleClosePosition = async (tradeId: number | string) => {
    try {
      if (typeof tradeId === 'number') {
        await apiService.closePosition(tradeId);
        showToast('Position closed successfully', 'success');
        await fetchBackendPositions();
      } else {
        setOpenPositions((prev) => prev.filter((p) => String(p.id) !== String(tradeId)));
        showToast('Position closed', 'success');
      }
    } catch (err: any) {
      showToast(`Failed to close: ${err.message}`, 'error');
    }
  };

  const handleModifyPosition = async (
    tradeId: number | string,
    sl: number | null,
    tp: number | null
  ) => {
    try {
      if (typeof tradeId === 'number') {
        await apiService.modifyPosition(tradeId, sl, tp);
        showToast('SL/TP updated', 'success');
        await fetchBackendPositions();
      }
    } catch (err: any) {
      showToast(`Failed to modify: ${err.message}`, 'error');
    }
  };

  const handleQuickTrade = async (sym: string, side: 'buy' | 'sell') => {
    const broker = sym.endsWith('=X') ? 'ctrader' : 'binance';
    const execPrice = parsePrice(price);
    const amount = parseFloat(manualAmount || '0.01');
    showToast(`Executing ${side.toUpperCase()} ${sym}...`, 'info');
    const result = await brokerService.executeTrade(broker, {
      symbol: sym,
      quantity: amount,
      type: 'market',
      side,
      price: execPrice,
    });
    if (result.success) {
      showToast(`Trade executed: ${result.orderId}`, 'success');
      setOpenPositions((prev) => [...prev, {
        id: result.orderId,
        asset: sym,
        type: side === 'buy' ? 'Long' : 'Short',
        entry: execPrice,
        mark: execPrice,
        pnl: 0,
        roe: 0,
        isPositive: true,
        amount,
      }]);
    } else {
      showToast(`Trade failed: ${result.error}`, 'error');
    }
  };

  return (
    <div className="flex h-screen bg-[#0A0A0B] text-zinc-100 overflow-hidden font-sans">
      {/* Sidebar */}
      <aside className={cn(
        "border-r border-zinc-800 flex flex-col items-center lg:items-stretch bg-[#0D0D0E] transition-all duration-300 ease-in-out relative z-30",
        (isSidebarHidden || (isZenMode && mode === 'ai')) ? "w-0 -translate-x-full lg:w-0" : "w-20 lg:w-64 translate-x-0"
      )}>
        <div className="p-6 flex items-center gap-3">
          <div className="w-10 h-10 bg-emerald-500 rounded-xl flex items-center justify-center shadow-lg shadow-emerald-500/20">
            <Zap className="text-black fill-current" size={24} />
          </div>
          <span className="hidden lg:block font-bold text-xl tracking-tight">QuantumTrade</span>
        </div>

        {/* Mode Switcher */}
        <div className="px-4 mb-6">
          <div className="bg-zinc-900/50 p-1 rounded-xl border border-zinc-800 flex">
            <button
              onClick={() => setMode('manual')}
              className={cn(
                "flex-1 flex items-center justify-center gap-2 py-2 text-xs font-semibold rounded-lg transition-all",
                mode === 'manual' ? "bg-zinc-800 text-white shadow-sm" : "text-zinc-500 hover:text-zinc-300"
              )}
            >
              <MousePointer2 size={14} /> Manual
            </button>
            <button
              onClick={() => setMode('ai')}
              className={cn(
                "flex-1 flex items-center justify-center gap-2 py-2 text-xs font-semibold rounded-lg transition-all",
                mode === 'ai' ? "bg-emerald-500 text-black shadow-lg shadow-emerald-500/20" : "text-zinc-500 hover:text-zinc-300"
              )}
            >
              <Cpu size={14} /> AI Bot
            </button>
          </div>
        </div>

        <nav className="flex-1 px-4 space-y-1">
          <NavItem icon={<LayoutDashboard size={20} />} label="Dashboard" active={mode === 'manual'} onClick={() => setMode('manual')} />
          <NavItem icon={<Cpu size={20} />} label="Agent Builder" active={mode === 'ai'} onClick={() => setMode('ai')} />
          <NavItem icon={<Zap size={20} />} label="Signals" active={mode === 'signals'} badge={loopRunning ? 'LIVE' : undefined} onClick={() => setMode('signals')} />
          <NavItem icon={<BrainCircuit size={20} />} label="Opinion Layer" active={mode === 'opinion'} onClick={() => setMode('opinion')} />
          <NavItem icon={<History size={20} />} label="Backtesting" active={mode === 'backtest'} onClick={() => setMode('backtest')} />
          <NavItem icon={<Activity size={20} />} label="Markets" active={mode === 'markets'} onClick={() => setMode('markets')} />
          <NavItem icon={<PieChart size={20} />} label="Portfolio" active={mode === 'portfolio'} onClick={() => setMode('portfolio')} />
          <NavItem icon={<FlaskConical size={20} />} label="Paper Trading" active={mode === 'paper'} onClick={() => setMode('paper')} />
          <NavItem icon={<Wallet size={20} />} label="Wallet" active={mode === 'wallet'} onClick={() => setMode('wallet')} />
          <div className="pt-2 mt-2 border-t border-zinc-800/50" />
          <NavItem icon={<Globe size={20} />} label="System Status" active={mode === 'status'} badge={backendConnected ? 'OK' : undefined} onClick={() => setMode('status')} />
          <NavItem icon={<Settings size={20} />} label="Settings" active={mode === 'settings'} onClick={() => setMode('settings')} />
        </nav>

        <div className="p-4 border-t border-zinc-800 space-y-2">
          <div className="lg:flex items-center gap-3 p-3 rounded-xl hover:bg-white/5 cursor-pointer transition-colors">
            <div className="w-8 h-8 rounded-full bg-gradient-to-tr from-emerald-500 to-blue-500 flex-shrink-0" />
            <div className="hidden lg:block overflow-hidden">
              <p className="text-sm font-medium truncate">Valgutom</p>
              <p className="text-xs text-emerald-500 font-mono font-medium truncate">${portfolioBalance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</p>
            </div>
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 flex flex-col overflow-hidden relative" style={{ marginRight: newsPanel ? '384px' : '0', transition: 'margin-right 0.3s ease' }}>
        {/* Top Header */}
        <header className="h-16 border-b border-zinc-800 flex items-center justify-between px-6 bg-[#0D0D0E]/50 backdrop-blur-xl z-10">
          <div className="flex items-center gap-8">
            <button
              onClick={() => setIsSidebarHidden(!isSidebarHidden)}
              className="p-2 hover:bg-white/5 rounded-lg text-zinc-400 transition-colors"
              title={isSidebarHidden ? "Show Sidebar" : "Hide Sidebar"}
              aria-label={isSidebarHidden ? "Show Sidebar" : "Hide Sidebar"}
            >
              <Layers size={20} className={cn(isSidebarHidden ? "text-emerald-400" : "")} />
            </button>
            <div className="flex items-center gap-3">
              <h2 className="text-lg font-semibold">
                {mode === 'manual' ? 'BTC / USDT' :
                  mode === 'ai' ? 'AI Workflow Builder' :
                    mode === 'backtest' ? 'Backtesting Engine' :
                      mode === 'settings' ? 'System Settings' :
                        mode === 'markets' ? 'Markets Overview' :
                          mode === 'portfolio' ? 'Portfolio Performance' :
                            mode === 'paper' ? 'Paper Trading Lab' :
                              mode === 'wallet' ? 'Wallet & Transfers' :
                              mode === 'signals' ? 'AI Trading Signals' :
                                mode === 'opinion' ? 'Opinion Layer' :
                                mode === 'status' ? 'System Status' : ''}
              </h2>
              {mode === 'ai' && (
                <button
                  onClick={() => setIsZenMode(!isZenMode)}
                  aria-label="Toggle Zen Mode"
                  className={cn(
                    "px-3 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider transition-all border",
                    isZenMode
                      ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/30"
                      : "bg-zinc-800 text-zinc-500 border-zinc-700 hover:text-zinc-300"
                  )}
                >
                  {isZenMode ? 'Zen Mode Active' : 'Zen Mode'}
                </button>
              )}
              {mode === 'manual' && (
                <span className={cn(
                  "text-sm font-mono flex items-center gap-1",
                  pnlPercent >= 0 ? "text-emerald-400" : "text-rose-400"
                )}>
                  <TrendingUp size={14} className={cn(pnlPercent < 0 && "rotate-180")} />
                  {pnlPercent >= 0 ? '+' : ''}{pnlPercent.toFixed(2)}%
                </span>
              )}
            </div>
          </div>

          <div className="flex items-center gap-4">
            <div className={cn(
              "hidden xl:flex items-center gap-2 px-3 py-1.5 border rounded-lg transition-colors",
              configService.getSecret('CTRADER_ACCESS_TOKEN')
                ? "bg-indigo-500/10 border-indigo-500/30"
                : "bg-zinc-800/50 border-zinc-700/50"
            )}>
              <Globe size={14} className={cn(configService.getSecret('CTRADER_ACCESS_TOKEN') ? "text-indigo-400" : "text-zinc-600")} />
              <span className={cn("text-[10px] font-bold uppercase", configService.getSecret('CTRADER_ACCESS_TOKEN') ? "text-indigo-300" : "text-zinc-600")}>
                cTrader OpenAPI
              </span>
              <div className={cn("w-1.5 h-1.5 rounded-full", configService.getSecret('CTRADER_ACCESS_TOKEN') ? "bg-emerald-500 animate-pulse" : "bg-zinc-600")} />
              <span className="text-[10px] text-zinc-500 font-medium">
                {configService.getSecret('CTRADER_ACCESS_TOKEN') ? 'Connected' : 'Disconnected'}
              </span>
            </div>

            <div className={cn(
              "hidden xl:flex items-center gap-2 px-3 py-1.5 border rounded-lg transition-colors",
              configService.getSecret('BINANCE_API_KEY')
                ? "bg-amber-500/10 border-amber-500/30"
                : "bg-zinc-800/50 border-zinc-700/50"
            )}>
              <Zap size={14} className={cn(configService.getSecret('BINANCE_API_KEY') ? "text-amber-400" : "text-zinc-600")} />
              <span className={cn("text-[10px] font-bold uppercase", configService.getSecret('BINANCE_API_KEY') ? "text-amber-300" : "text-zinc-600")}>
                Binance Spot
              </span>
              <div className={cn("w-1.5 h-1.5 rounded-full", configService.getSecret('BINANCE_API_KEY') ? "bg-emerald-500 animate-pulse" : "bg-zinc-600")} />
              <span className="text-[10px] text-zinc-500 font-medium">
                {configService.getSecret('BINANCE_API_KEY') ? 'Connected' : 'Disconnected'}
              </span>
            </div>
            <div className="relative hidden sm:block">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" size={16} />
              <input
                type="text"
                placeholder="Search..."
                aria-label="Search"
                className="bg-zinc-900 border border-zinc-800 rounded-lg py-1.5 pl-10 pr-4 text-sm focus:outline-none focus:border-emerald-500/50 transition-colors w-64"
              />
            </div>
            <button
              onClick={() => setIsChatOpen(true)}
              aria-label="Open Gemini AI Chat"
              className="p-2 text-zinc-400 hover:text-white transition-colors relative"
            >
              <Sparkles size={20} className="text-emerald-400" />
            </button>
            <button
              onClick={() => setNewsPanel(!newsPanel)}
              aria-label="Toggle News & Data Panel"
              className={`p-2 transition-colors relative ${
                newsPanel ? 'text-emerald-400' : 'text-zinc-400 hover:text-white'
              }`}
            >
              <Newspaper size={20} />
            </button>
            <div className="relative">
              <button
                className="p-2 text-zinc-400 hover:text-white transition-colors relative"
                onClick={() => setIsNotificationsOpen(!isNotificationsOpen)}
                aria-label="Toggle notifications"
              >
                <Bell size={20} />
                <span className="absolute top-2 right-2 w-2 h-2 bg-emerald-500 rounded-full border-2 border-[#0A0A0B]" />
              </button>

              <AnimatePresence>
                {isNotificationsOpen && (
                  <motion.div
                    initial={{ opacity: 0, y: 10, scale: 0.95 }}
                    animate={{ opacity: 1, y: 0, scale: 1 }}
                    exit={{ opacity: 0, y: 10, scale: 0.95 }}
                    className="absolute right-0 top-full mt-4 w-80 bg-zinc-900 border border-zinc-800 rounded-2xl shadow-xl overflow-hidden z-50"
                  >
                    <div className="p-4 border-b border-zinc-800 flex items-center justify-between">
                      <h3 className="font-semibold text-sm">Notifications</h3>
                      <button
                        className="text-[10px] text-emerald-400 hover:text-emerald-300 font-medium"
                        onClick={() => setIsNotificationsOpen(false)}
                        aria-label="Mark all as read"
                      >
                        Mark all as read
                      </button>
                    </div>
                    <div className="max-h-96 overflow-y-auto">
                      {[
                        { title: 'AI Workflow Executed', desc: 'RSI Reversal trigger fired for BTC/USDT. Order placed.', time: 'Just now', type: 'info' },
                        { title: 'Risk Limit Warning', desc: 'Daily drawdown limit approaches 80%. Consider pausing agents.', time: '2h ago', type: 'warn' },
                        { title: 'Deposit Confirmed', desc: 'Successfully deposited 5,000 USDT to Wallet.', time: '5h ago', type: 'success' },
                        { title: 'API Disconnected', desc: 'Missing Binance API keys. Re-check settings.', time: '1d ago', type: 'error' },
                      ].map((notif, i) => (
                        <div key={i} className="p-4 border-b border-zinc-800/50 hover:bg-white/5 transition-colors cursor-pointer flex gap-3">
                          <div className={cn(
                            "w-2 h-2 rounded-full mt-1.5 flex-shrink-0",
                            notif.type === 'info' ? "bg-blue-500" :
                              notif.type === 'warn' ? "bg-amber-500" :
                                notif.type === 'success' ? "bg-emerald-500" : "bg-rose-500"
                          )} />
                          <div>
                            <p className="text-sm font-medium text-white">{notif.title}</p>
                            <p className="text-xs text-zinc-400 mt-1">{notif.desc}</p>
                            <p className="text-[10px] text-zinc-600 mt-2 font-mono uppercase tracking-widest">{notif.time}</p>
                          </div>
                        </div>
                      ))}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </div>
        </header>

        <AnimatePresence mode="wait">
          {mode === 'manual' ? (
            <motion.div
              key="manual"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              className="flex-1 flex flex-col overflow-hidden"
            >
              {/* ── TOP: 5 Mini Candlestick Charts ─────────────────────────── */}
              <div className="grid grid-cols-5 gap-2 p-3 border-b border-zinc-800 bg-[#0d0d0f] flex-shrink-0">
                {WATCHLIST.map((sym) => (
                  <MiniCandleChart
                    key={sym.symbol}
                    symbol={sym.symbol}
                    displayName={sym.display}
                    signal={null}
                    isSelected={selectedSymbol === sym.symbol}
                    onSelect={setSelectedSymbol}
                    onQuickTrade={handleQuickTrade}
                  />
                ))}
              </div>

              {/* ── MAIN: Active Positions + Trade Entry ───────────────────── */}
              <div className="flex-1 overflow-y-auto p-4">

                {/* Active Positions Header */}
                <div className="flex items-center justify-between mb-4">
                  <h2 className="text-lg font-bold text-white flex items-center gap-2">
                    Active Positions
                    <span className="text-xs font-mono text-zinc-500 bg-zinc-900 px-2 py-0.5 rounded-full border border-zinc-800">
                      {allPositions.length} open
                    </span>
                  </h2>
                  <button
                    onClick={fetchBackendPositions}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider rounded-lg bg-zinc-800 text-zinc-300 hover:bg-zinc-700 border border-zinc-700 transition-colors"
                  >
                    <RefreshCw size={11} className={positionsLoading ? 'animate-spin' : ''} />
                    Refresh
                  </button>
                </div>

                {/* Active Trade Cards Grid */}
                <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 mb-6">
                  {allPositions.length === 0 ? (
                    <div className="col-span-2 text-center py-12 text-zinc-500 text-sm">
                      {positionsLoading
                        ? 'Loading positions...'
                        : 'No active positions. Use the trade panel below to open one.'}
                    </div>
                  ) : (
                    allPositions.map((pos) => (
                      <ActiveTradeCard
                        key={String(pos.id)}
                        trade={pos}
                        onClose={handleClosePosition}
                        onModify={handleModifyPosition}
                      />
                    ))
                  )}
                </div>

                {/* ── Bottom: Trade Entry + Order Book + Recent Trades ───────── */}
                <div className="grid grid-cols-12 gap-4">

                  {/* Order Form */}
                  <div className="col-span-12 lg:col-span-4 bg-[#141416] border border-zinc-800 rounded-xl p-4">
                    <div className="text-[10px] uppercase text-zinc-500 font-bold tracking-wider mb-3">
                      New Order — {selectedSymbol}
                    </div>
                    <div className="flex bg-zinc-900 rounded-xl p-1 mb-4">
                      <button
                        onClick={() => setActiveTab('buy')}
                        className={cn('flex-1 py-2 text-xs font-semibold rounded-lg transition-all', activeTab === 'buy' ? 'bg-emerald-500 text-black' : 'text-zinc-500')}
                      >Buy</button>
                      <button
                        onClick={() => setActiveTab('sell')}
                        className={cn('flex-1 py-2 text-xs font-semibold rounded-lg transition-all', activeTab === 'sell' ? 'bg-rose-500 text-white' : 'text-zinc-500')}
                      >Sell</button>
                    </div>
                    <div className="space-y-3">
                      <div className="space-y-1">
                        <label className="text-[9px] uppercase text-zinc-500 font-bold tracking-wider">Symbol</label>
                        <select
                          value={selectedSymbol}
                          onChange={(e) => setSelectedSymbol(e.target.value)}
                          className="w-full bg-zinc-900 border border-zinc-800 rounded-xl py-2 px-3 text-xs font-mono text-white focus:outline-none focus:border-emerald-500/50"
                        >
                          {WATCHLIST.map((w) => (
                            <option key={w.symbol} value={w.symbol}>{w.display}</option>
                          ))}
                        </select>
                      </div>
                      <div className="space-y-1">
                        <label className="text-[9px] uppercase text-zinc-500 font-bold tracking-wider">Order Type</label>
                        <select
                          value={manualOrderType}
                          onChange={(e) => setManualOrderType(e.target.value)}
                          className="w-full bg-zinc-900 border border-zinc-800 rounded-xl py-2 px-3 text-xs font-mono text-white focus:outline-none focus:border-emerald-500/50"
                        >
                          <option value="market">Market</option>
                          <option value="limit">Limit</option>
                        </select>
                      </div>
                      <div className="space-y-1">
                        <label className="text-[9px] uppercase text-zinc-500 font-bold tracking-wider">Price</label>
                        <input type="text" value={price} onChange={(e) => setPrice(e.target.value)} className="w-full bg-zinc-900 border border-zinc-800 rounded-xl py-2 px-3 text-xs font-mono text-white focus:outline-none focus:border-emerald-500/50" />
                      </div>
                      <div className="space-y-1">
                        <label className="text-[9px] uppercase text-zinc-500 font-bold tracking-wider">Amount</label>
                        <input type="number" step="0.001" value={manualAmount} onChange={(e) => setManualAmount(e.target.value)} className="w-full bg-zinc-900 border border-zinc-800 rounded-xl py-2 px-3 text-xs font-mono text-white focus:outline-none focus:border-emerald-500/50" />
                      </div>
                      <div className="grid grid-cols-2 gap-2">
                        <div className="space-y-1">
                          <label className="text-[9px] uppercase text-zinc-500 font-bold tracking-wider">Stop Loss</label>
                          <input type="number" step="any" value={manualSl} onChange={(e) => setManualSl(e.target.value)} placeholder="Optional" className="w-full bg-zinc-900 border border-zinc-800 rounded-xl py-2 px-3 text-xs font-mono text-white focus:outline-none focus:border-rose-500/50" />
                        </div>
                        <div className="space-y-1">
                          <label className="text-[9px] uppercase text-zinc-500 font-bold tracking-wider">Take Profit</label>
                          <input type="number" step="any" value={manualTp} onChange={(e) => setManualTp(e.target.value)} placeholder="Optional" className="w-full bg-zinc-900 border border-zinc-800 rounded-xl py-2 px-3 text-xs font-mono text-white focus:outline-none focus:border-emerald-500/50" />
                        </div>
                      </div>
                      <button
                        onClick={async () => {
                          const amount = parseFloat(manualAmount || '0.01');
                          const broker = selectedSymbol.endsWith('=X') ? 'ctrader' : 'binance';
                          const execPrice = parsePrice(price);
                          showToast(`Initiating ${activeTab.toUpperCase()} ${selectedSymbol}`, 'info');
                          const result = await brokerService.executeTrade(broker, {
                            symbol: selectedSymbol,
                            quantity: amount,
                            type: manualOrderType as 'market' | 'limit',
                            side: activeTab as 'buy' | 'sell',
                            price: execPrice,
                          });
                          if (result.success) {
                            showToast(`Order placed: ${result.orderId}`, 'success');
                            const cost = amount * execPrice;
                            setPortfolioBalance((prev) => prev - (activeTab === 'buy' ? cost : -cost));
                            setOpenPositions((prev) => [...prev, {
                              id: result.orderId,
                              asset: selectedSymbol,
                              type: activeTab === 'buy' ? 'Long' : 'Short',
                              entry: execPrice,
                              mark: execPrice,
                              pnl: 0,
                              roe: 0,
                              isPositive: true,
                              amount,
                            }]);
                            setRecentTrades((prev) => [{
                              price: execPrice.toFixed(2),
                              amount: amount.toFixed(4),
                              time: new Date().toLocaleTimeString(),
                              type: activeTab === 'buy' ? 'bid' : 'ask',
                            }, ...prev].slice(0, 50));
                          } else {
                            showToast(`Order failed: ${result.error}`, 'error');
                          }
                        }}
                        className={cn('w-full py-3 rounded-xl font-bold text-sm transition-all', activeTab === 'buy' ? 'bg-emerald-500 text-black shadow-emerald-500/20 shadow-lg' : 'bg-rose-500 text-white shadow-rose-500/20 shadow-lg')}
                      >
                        {activeTab === 'buy' ? `Buy ${selectedSymbol}` : `Sell ${selectedSymbol}`}
                      </button>
                    </div>
                  </div>

                  {/* Order Book */}
                  <div className="col-span-12 lg:col-span-4 bg-[#141416] border border-zinc-800 rounded-xl overflow-hidden flex flex-col" style={{ maxHeight: 480 }}>
                    <div className="px-4 py-3 border-b border-zinc-800 flex items-center justify-between flex-shrink-0">
                      <h3 className="text-xs font-semibold">Order Book</h3>
                      <span className="text-[10px] text-zinc-500 font-mono">{selectedSymbol}</span>
                    </div>
                    <div className="flex-1 overflow-y-auto py-1 scrollbar-hide">
                      <div className="space-y-0.5 mb-1">
                        {(depth?.asks || []).slice().reverse().map(([p, a], i) => (
                          <OrderBookRow
                            key={`ask-${i}`}
                            price={p.toFixed(2)}
                            amount={a.toFixed(4)}
                            total={(p * a).toFixed(2)}
                            type="ask"
                            depth={Math.min(100, (a / Math.max(1, ...(depth?.asks || []).map((d) => d[1]))) * 100)}
                          />
                        ))}
                      </div>
                      <div className="px-4 py-2 bg-zinc-900/50 flex items-center justify-between border-y border-zinc-800">
                        <span className="font-bold font-mono text-emerald-400">{price}</span>
                        <span className="text-[9px] text-zinc-500">Mark</span>
                      </div>
                      <div className="space-y-0.5 mt-1">
                        {(depth?.bids || []).map(([p, a], i) => (
                          <OrderBookRow
                            key={`bid-${i}`}
                            price={p.toFixed(2)}
                            amount={a.toFixed(4)}
                            total={(p * a).toFixed(2)}
                            type="bid"
                            depth={Math.min(100, (a / Math.max(1, ...(depth?.bids || []).map((d) => d[1]))) * 100)}
                          />
                        ))}
                      </div>
                    </div>
                  </div>

                  {/* Recent Trades */}
                  <div className="col-span-12 lg:col-span-4 bg-[#141416] border border-zinc-800 rounded-xl overflow-hidden flex flex-col" style={{ maxHeight: 480 }}>
                    <div className="px-4 py-3 border-b border-zinc-800 flex-shrink-0">
                      <h3 className="text-xs font-semibold">Recent Trades</h3>
                    </div>
                    <div className="flex-1 overflow-y-auto py-1 scrollbar-hide">
                      {recentTrades.length === 0 ? (
                        <div className="px-4 py-8 text-center text-zinc-500 text-xs italic">No trades yet.</div>
                      ) : (
                        recentTrades.map((t, i) => (
                          <div key={i} className="flex items-center justify-between px-4 py-1.5 text-[11px] font-mono hover:bg-white/5">
                            <span className={cn(t.type === 'bid' ? 'text-emerald-400' : 'text-rose-400')}>{t.price}</span>
                            <span className="text-zinc-400">{t.amount}</span>
                            <span className="text-zinc-600">{t.time}</span>
                          </div>
                        ))
                      )}
                    </div>
                  </div>

                </div>
              </div>
            </motion.div>
) : mode === 'ai' ? (
            <motion.div
              key="ai"
              initial={{ opacity: 0, scale: 0.98 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 1.02 }}
              className="flex-1 flex overflow-hidden"
            >
              {/* AI Workflow Builder View */}
              <div className="flex-1 relative">
                <ReactFlow
                  nodes={nodes}
                  edges={edges}
                  onNodesChange={onNodesChange}
                  onEdgesChange={onEdgesChange}
                  onConnect={onConnect}
                  nodeTypes={nodeTypes}
                  fitView
                  className="bg-[#0A0A0B]"
                >
                  <Background color="#27272a" gap={20} />
                  <Controls className="bg-zinc-900 border-zinc-800 fill-white" />
                  <MiniMap
                    nodeColor="#10b981"
                    maskColor="rgba(0,0,0,0.5)"
                    className="bg-zinc-900 border border-zinc-800 rounded-xl"
                  />

                  <Panel position="top-left" className={cn("flex flex-col gap-2 transition-all duration-300 max-h-[80vh] w-[260px]", isZenMode && "opacity-0 pointer-events-none -translate-x-10")}>
                    <div className="bg-[#141416] border border-zinc-800 rounded-xl p-4 shadow-2xl flex-shrink-0">
                      <div className="flex items-center justify-between mb-4">
                        <h3 className="text-sm font-bold flex items-center gap-2">
                          <Layers size={16} className="text-emerald-400" /> Workflows
                        </h3>
                        <button
                          onClick={createNewWorkflow}
                          className="p-1 hover:bg-white/5 rounded-md text-zinc-500 hover:text-emerald-400 transition-colors"
                        >
                          <Plus size={16} />
                        </button>
                      </div>
                      <div className="space-y-2 max-h-[150px] overflow-y-auto pr-2 scrollbar-default">
                        {workflows.length === 0 && (
                          <p className="text-[10px] text-zinc-500 italic text-center py-4">No saved workflows</p>
                        )}
                        {workflows.map(wf => (
                          <div
                            key={wf.id}
                            className={cn(
                              "group flex items-center justify-between p-2 rounded-lg border transition-all cursor-pointer",
                              activeWorkflowId === wf.id ? "bg-emerald-500/10 border-emerald-500/30" : "bg-zinc-900/50 border-zinc-800 hover:border-zinc-700"
                            )}
                            onClick={() => loadWorkflow(wf.id)}
                          >
                            <div className="flex items-center gap-2 overflow-hidden">
                              <div className={cn(
                                "w-1.5 h-1.5 rounded-full flex-shrink-0",
                                wf.isRunning ? "bg-emerald-500 animate-pulse" : "bg-zinc-700"
                              )} />
                              <span className="text-xs font-medium truncate">{wf.name}</span>
                            </div>
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                toggleWorkflowRun(wf.id);
                              }}
                              className={cn(
                                "p-1 rounded transition-colors",
                                wf.isRunning ? "text-rose-400 hover:bg-rose-500/10" : "text-emerald-400 hover:bg-emerald-500/10"
                              )}
                            >
                              {wf.isRunning ? <Pause size={12} /> : <Play size={12} />}
                            </button>
                          </div>
                        ))}
                      </div>
                    </div>

                    <div className="bg-[#141416] border border-zinc-800 rounded-xl p-4 shadow-2xl flex-1 flex flex-col min-h-0 overflow-hidden">
                      <h3 className="text-sm font-bold mb-4 flex-shrink-0 flex items-center gap-2">
                        <Layers size={16} className="text-emerald-400" /> Components
                      </h3>
                      <div className="space-y-2 flex-1 overflow-y-auto min-h-0 pr-2 scrollbar-default">
                        <DraggableComponent icon={<Activity size={14} />} label="RSI Indicator" type="Trigger" />
                        <DraggableComponent icon={<Clock size={14} />} label="Time Delay" type="Condition" />
                        <DraggableComponent icon={<Zap size={14} />} label="Market Buy" type="Action" />
                        <DraggableComponent icon={<TrendingUp size={14} />} label="Take Profit" type="Condition" />
                        <DraggableComponent icon={<Shield size={14} />} label="Stop Loss" type="Action" />
                        <DraggableComponent icon={<Globe size={14} />} label="Webhook" type="Trigger" />
                        <DraggableComponent icon={<Globe size={14} />} label="cTrader API" type="Integration" />
                        <DraggableComponent icon={<Zap size={14} />} label="Binance API" type="Integration" />
                        <DraggableComponent icon={<Database size={14} />} label="PostgreSQL" type="Integration" />
                        <DraggableComponent icon={<Cloud size={14} />} label="Supabase" type="Integration" />
                        <DraggableComponent icon={<BarChart3 size={14} />} label="Grafana" type="Integration" />
                        <DraggableComponent icon={<Sparkles size={14} />} label="Gemini Agent" type="Action" />
                      </div>
                      <div className="pt-4 mt-2 border-t border-zinc-800 flex-shrink-0">
                        <button
                          onClick={addNewNode}
                          className="w-full py-2.5 bg-emerald-500 hover:bg-emerald-400 text-black rounded-xl text-xs font-bold flex items-center justify-center gap-2 transition-all shadow-lg shadow-emerald-500/20"
                        >
                          <Plus size={16} /> Create New Node
                        </button>
                      </div>
                    </div>
                  </Panel>

                  <Panel position="top-right" className={cn("flex flex-col gap-2 transition-all duration-300", isZenMode && "opacity-0 pointer-events-none translate-x-10")}>
                    <div className="bg-[#141416] border border-zinc-800 rounded-xl p-4 shadow-2xl min-w-[200px]">
                      <h3 className="text-sm font-bold mb-4 flex items-center gap-2">
                        <Cpu size={16} className="text-emerald-400" /> Agent Status
                      </h3>
                      <div className="space-y-4">
                        <div className="flex items-center justify-between">
                          <span className="text-xs text-zinc-500">Status</span>
                          <span className="text-xs font-bold text-emerald-400 flex items-center gap-1">
                            <div className="w-1.5 h-1.5 bg-emerald-500 rounded-full animate-pulse" /> Running
                          </span>
                        </div>
                        <div className="flex items-center justify-between">
                          <span className="text-xs text-zinc-500">Total Trades</span>
                          <span className="text-xs font-bold">142</span>
                        </div>
                        <div className="flex items-center justify-between">
                          <span className="text-xs text-zinc-500">Win Rate</span>
                          <span className="text-xs font-bold text-emerald-400">68.4%</span>
                        </div>
                        <div className="pt-2 border-t border-zinc-800 flex flex-col gap-2">
                          <button
                            onClick={handleWorkflowOptimization}
                            disabled={isOptimizing}
                            className="w-full bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-400 py-2 rounded-lg text-[10px] font-bold flex items-center justify-center gap-1 border border-indigo-500/20 transition-all"
                          >
                            <BrainCircuit size={12} /> {isOptimizing ? 'Optimizing...' : 'Optimize with Gemini'}
                          </button>
                          <div className="flex gap-2">
                            <button className="flex-1 bg-zinc-800 hover:bg-zinc-700 py-2 rounded-lg text-[10px] font-bold flex items-center justify-center gap-1">
                              <Pause size={12} /> Pause
                            </button>
                            <button
                              onClick={saveWorkflow}
                              className="flex-1 bg-emerald-500 text-black py-2 rounded-lg text-[10px] font-bold flex items-center justify-center gap-1"
                            >
                              <Save size={12} /> Save
                            </button>
                          </div>
                        </div>
                      </div>
                    </div>

                    {/* Node Properties Panel */}
                    <AnimatePresence>
                      {selectedNodes.length === 1 && (
                        <motion.div
                          initial={{ x: 50, opacity: 0 }}
                          animate={{ x: 0, opacity: 1 }}
                          exit={{ x: 50, opacity: 0 }}
                          className="bg-[#141416] border border-zinc-800 rounded-xl p-4 shadow-2xl min-w-[260px] mt-2"
                        >
                          <h3 className="text-sm font-bold mb-4 flex items-center gap-2">
                            <Settings size={16} className="text-emerald-400" /> Node Properties
                          </h3>
                          <div className="space-y-4">
                            <div className="space-y-1.5">
                              <label className="text-[10px] uppercase text-zinc-500 font-bold">Label</label>
                              <input
                                type="text"
                                value={selectedNodes[0].data.label as string}
                                onChange={(e) => updateNodeData(selectedNodes[0].id, { label: e.target.value })}
                                aria-label="Node Label"
                                className="w-full bg-zinc-900 border border-zinc-800 rounded-lg py-2 px-3 text-xs focus:outline-none focus:border-emerald-500/50"
                              />
                            </div>
                            <div className="space-y-1.5">
                              <label className="text-[10px] uppercase text-zinc-500 font-bold">Type</label>
                              <select
                                value={selectedNodes[0].data.type as string}
                                onChange={(e) => updateNodeData(selectedNodes[0].id, { type: e.target.value })}
                                aria-label="Node Type"
                                className="w-full bg-zinc-900 border border-zinc-800 rounded-lg py-2 px-3 text-xs focus:outline-none focus:border-emerald-500/50 appearance-none"
                              >
                                <option value="Trigger">Trigger</option>
                                <option value="Condition">Condition</option>
                                <option value="Action">Action</option>
                                <option value="Integration">Integration</option>
                              </select>
                            </div>
                            <div className="space-y-1.5">
                              <label className="text-[10px] uppercase text-zinc-500 font-bold">Custom Logic</label>
                              <textarea
                                placeholder="Enter expression..."
                                aria-label="Custom Logic Expression"
                                className="w-full bg-zinc-900 border border-zinc-800 rounded-lg py-2 px-3 text-xs focus:outline-none focus:border-emerald-500/50 min-h-[80px] resize-none"
                              />
                            </div>
                          </div>
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </Panel>

                  {/* Batch Actions Panel */}
                  <AnimatePresence>
                    {selectedNodes.length > 1 && (
                      <Panel position="bottom-center">
                        <motion.div
                          initial={{ y: 50, opacity: 0 }}
                          animate={{ y: 0, opacity: 1 }}
                          exit={{ y: 50, opacity: 0 }}
                          className="bg-[#141416] border border-emerald-500/50 rounded-2xl p-4 shadow-2xl flex items-center gap-6 mb-8"
                        >
                          <div className="flex items-center gap-3 pr-6 border-r border-zinc-800">
                            <div className="w-8 h-8 bg-emerald-500/20 rounded-lg flex items-center justify-center text-emerald-400 font-bold text-xs">
                              {selectedNodes.length}
                            </div>
                            <span className="text-sm font-semibold">Nodes Selected</span>
                          </div>

                          <div className="flex items-center gap-2">
                            <button
                              onClick={handleGroupNodes}
                              className="flex items-center gap-2 px-4 py-2 bg-zinc-800 hover:bg-zinc-700 rounded-xl text-xs font-bold transition-colors"
                            >
                              <Layers size={14} className="text-emerald-400" /> Group Selected
                            </button>

                            <div className="h-8 w-px bg-zinc-800 mx-2" />

                            <div className="flex items-center gap-1">
                              {['bg-blue-500/20', 'bg-amber-500/20', 'bg-emerald-500/20', 'bg-rose-500/20'].map(color => (
                                <button
                                  key={color}
                                  onClick={() => handleBatchColorChange(color)}
                                  className={cn("w-6 h-6 rounded-full border border-zinc-800 hover:scale-110 transition-transform", color.replace('/20', ''))}
                                  style={{ backgroundColor: color.includes('emerald') ? '#10b981' : color.includes('blue') ? '#3b82f6' : color.includes('amber') ? '#f59e0b' : '#f43f5e' }}
                                />
                              ))}
                            </div>

                            <div className="h-8 w-px bg-zinc-800 mx-2" />

                            <button
                              onClick={handleBatchDelete}
                              className="flex items-center gap-2 px-4 py-2 bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 rounded-xl text-xs font-bold transition-colors"
                            >
                              <Trash2 size={14} /> Delete All
                            </button>
                          </div>
                        </motion.div>
                      </Panel>
                    )}
                  </AnimatePresence>
                  <Panel position="top-right" className={cn("flex flex-col gap-4 transition-all duration-300", isZenMode && "opacity-0 pointer-events-none translate-x-10")}>
                    {selectedNodes.length === 1 && (selectedNodes[0].data.type === 'Integration' || selectedNodes[0].data.type === 'Action') && (
                      <div className="space-y-4">
                        <NodeProperties
                          node={selectedNodes[0]}
                          onUpdate={updateNodeConfig}
                        />
                        <button
                          onClick={() => executeNodeTrade(selectedNodes[0])}
                          className="w-full bg-emerald-500 hover:bg-emerald-400 text-black font-bold py-3 rounded-xl shadow-lg shadow-emerald-500/20 flex items-center justify-center gap-2 transition-all active:scale-95"
                        >
                          <Zap size={16} fill="currentColor" /> Execute Trade
                        </button>
                      </div>
                    )}
                  </Panel>
                </ReactFlow>
              </div>
            </motion.div>
          ) : mode === 'backtest' ? (
            <motion.div
              key="backtest"
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -20 }}
              className="flex-1 overflow-y-auto p-6 flex flex-col gap-6"
            >
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-2xl font-bold tracking-tight">AI Backtesting Engine</h2>
                  <p className="text-zinc-500 text-sm">Test your workflows against historical PostgreSQL data</p>
                </div>
                <button
                  onClick={handleRunBacktest}
                  disabled={isBacktesting}
                  className={cn(
                    "px-6 py-3 rounded-xl font-bold text-sm flex items-center gap-2 transition-all",
                    isBacktesting ? "bg-zinc-800 text-zinc-500 cursor-not-allowed" : "bg-emerald-500 text-black hover:bg-emerald-400 shadow-lg shadow-emerald-500/20"
                  )}
                >
                  {isBacktesting ? (
                    <>
                      <div className="w-4 h-4 border-2 border-zinc-500 border-t-transparent rounded-full animate-spin" />
                      Running Backtest ({backtestProgress}%)
                    </>
                  ) : (
                    <>
                      <Play size={16} fill="currentColor" /> Run Backtest
                    </>
                  )}
                </button>
              </div>

              {backtestResults ? (
                <div className="grid grid-cols-12 gap-6">
                  {/* Stats Cards */}
                  <div className="col-span-12 grid grid-cols-1 md:grid-cols-4 gap-4">
                    <div className="bg-[#141416] border border-zinc-800 rounded-2xl p-6">
                      <p className="text-zinc-500 text-[10px] uppercase font-bold tracking-widest mb-1">Total Profit</p>
                      <p className={cn("text-2xl font-bold font-mono", backtestResults.totalProfit >= 0 ? "text-emerald-400" : "text-rose-400")}>
                        {backtestResults.totalProfit >= 0 ? '+' : ''}{backtestResults.totalProfit.toFixed(2)}
                      </p>
                    </div>
                    <div className="bg-[#141416] border border-zinc-800 rounded-2xl p-6">
                      <p className="text-zinc-500 text-[10px] uppercase font-bold tracking-widest mb-1">Win Rate</p>
                      <p className="text-2xl font-bold font-mono text-white">{backtestResults.winRate.toFixed(1)}%</p>
                    </div>
                    <div className="bg-[#141416] border border-zinc-800 rounded-2xl p-6">
                      <p className="text-zinc-500 text-[10px] uppercase font-bold tracking-widest mb-1">Total Trades</p>
                      <p className="text-2xl font-bold font-mono text-white">{backtestResults.totalTrades}</p>
                    </div>
                    <div className="bg-[#141416] border border-zinc-800 rounded-2xl p-6">
                      <p className="text-zinc-500 text-[10px] uppercase font-bold tracking-widest mb-1">Max Drawdown</p>
                      <p className="text-2xl font-bold font-mono text-rose-400">-{backtestResults.maxDrawdown}%</p>
                    </div>
                  </div>

                  {/* Equity Curve */}
                  <div className="col-span-12 lg:col-span-8 bg-[#141416] border border-zinc-800 rounded-2xl p-6 h-[400px] flex flex-col">
                    <h3 className="text-sm font-bold mb-6 flex items-center gap-2">
                      <LineChartIcon size={16} className="text-emerald-400" /> Equity Curve
                    </h3>
                    <div className="flex-1">
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={backtestResults.equityCurve}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" vertical={false} />
                          <XAxis
                            dataKey="time"
                            hide
                          />
                          <YAxis
                            domain={['auto', 'auto']}
                            stroke="#4b5563"
                            fontSize={10}
                            tickLine={false}
                            axisLine={false}
                            tickFormatter={(v) => `$${v}`}
                          />
                          <Tooltip
                            contentStyle={{ backgroundColor: '#111827', border: '1px solid #374151', borderRadius: '8px', fontSize: '10px' }}
                            itemStyle={{ color: '#10b981' }}
                          />
                          <Line
                            type="monotone"
                            dataKey="value"
                            stroke="#10b981"
                            strokeWidth={2}
                            dot={false}
                          />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  </div>

                  {/* AI Analysis Row */}
                  <div className="col-span-12 lg:col-span-4 bg-[#141416] border border-zinc-800 rounded-2xl p-6 flex flex-col">
                    <div className="flex items-center gap-2 mb-4">
                      <Sparkles size={16} className="text-emerald-400" />
                      <h3 className="text-sm font-bold">AI Performance Audit</h3>
                    </div>
                    <p className="text-xs text-zinc-500 mb-6 leading-relaxed">
                      Get deep insights into your strategy performance from Gemini. Analyze drawdowns, win rates, and get actionable suggestions.
                    </p>
                    <button
                      onClick={handleBacktestAnalysis}
                      disabled={isAiLoading}
                      className="mt-auto w-full py-4 bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-400 rounded-xl text-xs font-bold transition-all border border-indigo-500/20 flex items-center justify-center gap-2"
                    >
                      <BrainCircuit size={16} /> {isAiLoading ? 'Analyzing...' : 'Analyze with Gemini'}
                    </button>
                  </div>

                  {/* Trade History */}
                  <div className="col-span-12 lg:col-span-4 bg-[#141416] border border-zinc-800 rounded-2xl overflow-hidden flex flex-col h-[400px]">
                    <div className="px-6 py-4 border-b border-zinc-800">
                      <h3 className="text-sm font-bold">Trade History</h3>
                    </div>
                    <div className="flex-1 overflow-y-auto p-4 space-y-2 scrollbar-hide">
                      {backtestResults.trades.map((trade, i) => (
                        <div key={i} className="flex items-center justify-between p-3 bg-zinc-900/50 border border-zinc-800 rounded-xl">
                          <div className="flex items-center gap-3">
                            <div className={cn(
                              "w-8 h-8 rounded-lg flex items-center justify-center text-[10px] font-bold",
                              trade.type === 'buy' ? "bg-emerald-500/10 text-emerald-400" : "bg-rose-500/10 text-rose-400"
                            )}>
                              {trade.type.toUpperCase()}
                            </div>
                            <div>
                              <p className="text-[10px] font-mono text-white">${trade.price.toFixed(2)}</p>
                              <p className="text-[8px] text-zinc-500">{new Date(trade.time * 1000).toLocaleString()}</p>
                            </div>
                          </div>
                          {trade.profit !== undefined && (
                            <span className={cn("text-[10px] font-mono font-bold", trade.profit >= 0 ? "text-emerald-400" : "text-rose-400")}>
                              {trade.profit >= 0 ? '+' : ''}{trade.profit.toFixed(2)}
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* AI Analysis */}
                  {aiInsight && (
                    <div className="col-span-12 bg-emerald-500/5 border border-emerald-500/20 rounded-2xl p-6">
                      <div className="flex items-center gap-3 mb-4">
                        <Sparkles className="text-emerald-400" size={20} />
                        <h3 className="text-sm font-bold text-emerald-300">AI Performance Analysis</h3>
                      </div>
                      <p className="text-sm text-emerald-100/80 leading-relaxed italic">
                        {aiInsight}
                      </p>
                    </div>
                  )}
                </div>
              ) : (
                <div className="flex-1 flex flex-col items-center justify-center text-center p-12 bg-[#141416] border border-zinc-800 border-dashed rounded-3xl space-y-4">
                  <div className="w-16 h-16 bg-zinc-900 rounded-2xl flex items-center justify-center text-zinc-700">
                    <History size={32} />
                  </div>
                  <div>
                    <h3 className="text-lg font-bold">No Backtest Results</h3>
                    <p className="text-zinc-500 text-sm max-w-md mx-auto">
                      Configure your workflow in the Agent Builder, then click "Run Backtest" to see how it would have performed on historical data.
                    </p>
                  </div>
                </div>
              )}
            </motion.div>
          ) : mode === 'settings' ? (
            <motion.div
              key="settings"
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -20 }}
              className="flex-1 overflow-hidden flex flex-col"
            >
              <SettingsView />
            </motion.div>
          ) : mode === 'portfolio' ? (
            <PortfolioView key="portfolio" />
          ) : mode === 'paper' ? (
            <PaperTradingView />
          ) : mode === 'wallet' ? (
            <WalletView key="wallet" />
          ) : mode === 'markets' ? (
            <MarketsView key="markets" />
          ) : mode === 'signals' ? (
            <SignalsView />
          ) : mode === 'opinion' ? (
            <OpinionLayerView />
          ) : mode === 'status' ? (
            <StatusView key="status" />
          ) : null}
        </AnimatePresence>
      </main>

      <GeminiChat
        isOpen={isChatOpen}
        onClose={() => setIsChatOpen(false)}
        messages={messages}
        onSendMessage={handleChatSend}
        isLoading={isAiLoading}
      />


      {/* n8n-style Node Selector Modal */}
      <AnimatePresence>
        {isNodeSelectorOpen && (
          <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setIsNodeSelectorOpen(false)}
              className="absolute inset-0 bg-black/80 backdrop-blur-sm"
            />
            <motion.div
              initial={{ opacity: 0, scale: 0.95, y: 20 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95, y: 20 }}
              className="relative w-full max-w-2xl bg-[#0c0c0e] border border-zinc-800 rounded-3xl shadow-2xl flex flex-col max-h-[85vh] overflow-hidden"
            >
              {/* Header */}
              <div className="p-6 border-b border-zinc-800 flex-shrink-0 flex items-center justify-between bg-zinc-900/30">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 bg-emerald-500/10 rounded-xl flex items-center justify-center text-emerald-400">
                    <Plus size={24} />
                  </div>
                  <div>
                    <h2 className="text-xl font-bold tracking-tight">Add Node</h2>
                    <p className="text-zinc-500 text-xs">Select a component to add to your workflow</p>
                  </div>
                </div>
                <button
                  onClick={() => setIsNodeSelectorOpen(false)}
                  className="p-2 hover:bg-white/5 rounded-full text-zinc-500 transition-colors"
                >
                  <X size={20} />
                </button>
              </div>

              {/* Search */}
              <div className="p-6 border-b border-zinc-800 flex-shrink-0">
                <div className="relative">
                  <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-zinc-500" size={18} />
                  <input
                    autoFocus
                    type="text"
                    placeholder="Search nodes (e.g. RSI, Market Buy, PostgreSQL...)"
                    value={nodeSearch}
                    onChange={(e) => setNodeSearch(e.target.value)}
                    className="w-full bg-zinc-900 border border-zinc-800 rounded-2xl py-3.5 pl-12 pr-4 text-sm focus:outline-none focus:border-emerald-500/50 transition-all"
                  />
                </div>
              </div>

              {/* Node List */}
              <div className="flex-1 overflow-y-auto min-h-0 p-6 scrollbar-default">
                <div className="grid grid-cols-1 gap-4">
                  {[
                    {
                      category: 'Triggers', items: [
                        { label: 'RSI Indicator', type: 'Trigger', icon: Activity, desc: 'Triggers when RSI hits overbought/oversold levels' },
                        { label: 'MACD Crossover', type: 'Trigger', icon: TrendingUp, desc: 'Triggers on MACD line crossover' },
                        { label: 'Price Alert', type: 'Trigger', icon: TrendingUp, desc: 'Triggers when price crosses a specific level' },
                        { label: 'Volume Spike', type: 'Trigger', icon: Zap, desc: 'Triggers on unusual volume activity' },
                        { label: 'Schedule / Cron', type: 'Trigger', icon: Clock, desc: 'Run workflow on a fixed schedule' },
                        { label: 'Webhook', type: 'Trigger', icon: Globe, desc: 'Trigger workflow from external HTTP post' },
                      ]
                    },
                    {
                      category: 'Conditions', items: [
                        { label: 'Time Delay', type: 'Condition', icon: Clock, desc: 'Wait for a specific duration before continuing' },
                        { label: 'Trend Check', type: 'Condition', icon: TrendingUp, desc: 'Verify if market is in uptrend/downtrend' },
                        { label: 'Risk Check', type: 'Condition', icon: Shield, desc: 'Validate trade against risk parameters' },
                        { label: 'Drawdown Limit', type: 'Condition', icon: Settings, desc: 'Stop execution if drawdown exceeds threshold' },
                        { label: 'Data Exists', type: 'Condition', icon: Database, desc: 'Proceed only if required data is found' },
                      ]
                    },
                    {
                      category: 'Actions', items: [
                        { label: 'Market Buy', type: 'Action', icon: Zap, desc: 'Execute an immediate market buy order' },
                        { label: 'Market Sell', type: 'Action', icon: Zap, desc: 'Execute an immediate market sell order' },
                        { label: 'Limit Buy', type: 'Action', icon: Layers, desc: 'Place a limit buy order at specific price' },
                        { label: 'Limit Sell', type: 'Action', icon: Layers, desc: 'Place a limit sell order at specific price' },
                        { label: 'Take Profit', type: 'Action', icon: TrendingUp, desc: 'Set or update take profit levels' },
                        { label: 'Stop Loss', type: 'Action', icon: Shield, desc: 'Set or update stop loss levels' },
                        { label: 'Cancel All Orders', type: 'Action', icon: MessageSquare, desc: 'Cancel all active pending orders' },
                      ]
                    },
                    {
                      category: 'AI & Analysis', items: [
                        { label: 'Gemini Agent', type: 'Action', icon: Sparkles, desc: 'Process data using Google Gemini AI' },
                        { label: 'Sentiment Analysis', type: 'Action', icon: Activity, desc: 'Analyze market sentiment from news/social' },
                        { label: 'Data Extractor', type: 'Action', icon: Cpu, desc: 'Format unstructured text into JSON' },
                      ]
                    },
                    {
                      category: 'Integrations', items: [
                        { label: 'cTrader API', type: 'Integration', icon: Globe, desc: 'Connect to cTrader for execution' },
                        { label: 'Binance API', type: 'Integration', icon: Zap, desc: 'Connect to Binance for execution' },
                        { label: 'PostgreSQL', type: 'Integration', icon: Database, desc: 'Execute SQL queries on Postgres' },
                        { label: 'MySQL', type: 'Integration', icon: Database, desc: 'Execute SQL queries on MySQL' },
                        { label: 'Supabase', type: 'Integration', icon: Cloud, desc: 'Database & Auth via Supabase' },
                        { label: 'InfluxDB', type: 'Integration', icon: Activity, desc: 'Push time-series data to InfluxDB' },
                        { label: 'Grafana', type: 'Integration', icon: BarChart3, desc: 'Update Grafana dashboards' },
                        { label: 'Telegram', type: 'Integration', icon: MessageSquare, desc: 'Send alerts to Telegram' },
                        { label: 'Discord', type: 'Integration', icon: MessageSquare, desc: 'Send alerts via Discord Webhooks' },
                        { label: 'Slack', type: 'Integration', icon: MessageSquare, desc: 'Send alerts to Slack channels' },
                        { label: 'HTTP Request', type: 'Integration', icon: Globe, desc: 'Make an external API request' },
                      ]
                    },
                    {
                      category: 'Logic', items: [
                        { label: 'Switch', type: 'Condition', icon: Layers, desc: 'Route workflow based on conditions' },
                        { label: 'Filter', type: 'Condition', icon: Shield, desc: 'Only continue if data matches filter' },
                        { label: 'Merge', type: 'Condition', icon: Layers, desc: 'Combine multiple workflow streams into one' },
                        { label: 'Function', type: 'Action', icon: Cpu, desc: 'Execute custom JavaScript code' },
                      ]
                    }
                  ].map(cat => {
                    const filteredItems = cat.items.filter(item =>
                      item.label.toLowerCase().includes(nodeSearch.toLowerCase()) ||
                      item.desc.toLowerCase().includes(nodeSearch.toLowerCase())
                    );

                    if (filteredItems.length === 0) return null;

                    return (
                      <div key={cat.category} className="col-span-full space-y-3 mb-6">
                        <h3 className="text-[10px] uppercase tracking-widest text-zinc-500 font-bold px-2">{cat.category}</h3>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                          {filteredItems.map(item => (
                            <motion.button
                              initial={{ opacity: 0, scale: 0.95 }}
                              animate={{ opacity: 1, scale: 1 }}
                              whileHover={{ scale: 1.02, y: -2 }}
                              whileTap={{ scale: 0.98 }}
                              key={item.label}
                              onClick={() => createNode(item.type, item.label, item.icon)}
                              className="flex items-start gap-4 p-4 bg-zinc-900/40 backdrop-blur-md border border-zinc-800 rounded-2xl hover:border-emerald-500/50 hover:bg-emerald-500/5 transition-all text-left group shadow-lg"
                            >
                              <div className={cn(
                                "p-3 rounded-xl transition-all relative overflow-hidden shrink-0",
                                item.type === 'Trigger' ? "bg-blue-500/10 text-blue-400 group-hover:bg-blue-500/20" :
                                  item.type === 'Condition' ? "bg-amber-500/10 text-amber-400 group-hover:bg-amber-500/20" :
                                    item.type === 'Action' ? "bg-emerald-500/10 text-emerald-400 group-hover:bg-emerald-500/20" : "bg-zinc-800 text-zinc-400"
                              )}>
                                <div className="absolute inset-0 opacity-10 bg-white" />
                                {React.createElement(item.icon as any, { size: 20, className: "relative z-10" })}
                              </div>
                              <div className="flex-1 min-w-0">
                                <h4 className="text-sm font-bold mb-1 text-white group-hover:text-emerald-400 transition-colors">{item.label}</h4>
                                <p className="text-[10px] text-zinc-500 leading-relaxed line-clamp-2">{item.desc}</p>
                              </div>
                            </motion.button>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
      {/* News & Data Panel */}
      <NewsDataPanel isOpen={newsPanel} onClose={() => setNewsPanel(false)} />
    </div>
  );
}

function DraggableComponent({ icon, label, type }: { icon: React.ReactNode, label: string, type: string }) {
  return (
    <div className="flex items-center gap-3 p-2 rounded-lg bg-zinc-900 border border-zinc-800 hover:border-emerald-500/50 cursor-grab active:cursor-grabbing transition-colors group">
      <div className={cn(
        "p-1.5 rounded-md shrink-0",
        type === 'Trigger' ? "bg-blue-500/20 text-blue-400" :
          type === 'Condition' ? "bg-amber-500/20 text-amber-400" : "bg-emerald-500/20 text-emerald-400"
      )}>
        {icon}
      </div>
      <div className="flex-1">
        <p className="text-[9px] uppercase text-zinc-500 font-bold leading-none mb-0.5">{type}</p>
        <p className="text-xs font-medium text-zinc-300">{label}</p>
      </div>
      <Plus size={14} className="text-zinc-600 group-hover:text-emerald-400 transition-colors" />
    </div>
  );
}
