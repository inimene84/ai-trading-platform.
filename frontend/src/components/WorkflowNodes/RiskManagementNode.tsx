import React from 'react';
import { Handle, Position, NodeProps } from '@xyflow/react';
import { Shield, TrendingDown, TrendingUp, Move } from 'lucide-react';
import { motion } from 'motion/react';
import { cn } from '../../lib/utils';
import { RiskManagementConfig } from '../../services/workflowEngine';

interface RiskManagementNodeData {
  label: string;
  type: string;
  icon?: any;
  color?: string;
  config?: RiskManagementConfig;
  executing?: boolean;
  levels?: {
    stopLoss: number;
    takeProfit: number;
    trailingStop?: number;
  };
  entryPrice?: number;
}

export const RiskManagementNode: React.FC<NodeProps<RiskManagementNodeData>> = ({ data, selected }) => {
  const config = data.config;
  const isExecuting = data.executing;
  const levels = data.levels;
  const entryPrice = data.entryPrice;

  const stopLossType = config?.stopLossType || 'atr';
  const takeProfitRatio = config?.takeProfitRatio || 2;
  const useTrailing = config?.useTrailingStop || false;

  // Calculate risk:reward visualization
  const riskReward = levels?.stopLoss && levels?.takeProfit && entryPrice
    ? ((levels.takeProfit - entryPrice) / (entryPrice - levels.stopLoss)).toFixed(1)
    : takeProfitRatio;

  return (
    <motion.div
      initial={{ scale: 0.9, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      whileHover={{ y: -2 }}
      className={cn(
        "px-4 py-3 rounded-2xl bg-zinc-900/80 backdrop-blur-xl border transition-all min-w-[240px] relative",
        isExecuting ? "border-blue-500/50 shadow-[0_0_25px_rgba(59,130,246,0.3)]" :
        levels ? "border-rose-500/50 shadow-[0_0_20px_rgba(244,63,94,0.2)]" :
        selected ? "border-rose-500/50" : "border-zinc-800/50 hover:border-zinc-700"
      )}
    >
      {/* Input handle */}
      <Handle 
        type="target" 
        position={Position.Top} 
        className="w-2 h-2 bg-zinc-600 border-none !-top-1"
      />

      {/* Node content */}
      <div className="flex items-center gap-3">
        <div className={cn(
          "w-10 h-10 rounded-xl flex items-center justify-center relative overflow-hidden",
          "bg-gradient-to-br from-rose-500/20 to-rose-600/20"
        )}>
          <div className="absolute inset-0 opacity-30 bg-white" />
          <Shield size={20} className="relative z-10 text-rose-400" />
          
          {isExecuting && (
            <div className="absolute inset-0 animate-pulse bg-blue-500/20" />
          )}
        </div>

        <div className="flex-1 min-w-0">
          <p className="text-[9px] uppercase text-zinc-500 font-black tracking-[0.1em] leading-none mb-1">
            Risk Management
          </p>
          <p className="text-xs font-bold text-white leading-none truncate">{data.label}</p>
        </div>
      </div>

      {/* Configuration & Levels */}
      <div className="mt-3 pt-2 border-t border-zinc-800/50 space-y-2">
        {/* SL Type */}
        <div className="flex items-center justify-between text-[10px] font-mono">
          <span className="text-zinc-500 flex items-center gap-1">
            <TrendingDown size={10} className="text-rose-400" /> Stop Loss Type
          </span>
          <span className={stopLossType === 'atr' ? "text-amber-400" : "text-zinc-400"}>
            {stopLossType.toUpperCase()}
          </span>
        </div>

        {/* R:R Ratio */}
        <div className="flex items-center justify-between text-[10px] font-mono">
          <span className="text-zinc-500 flex items-center gap-1">
            <TrendingUp size={10} className="text-emerald-400" /> Target R:R
          </span>
          <span className="text-emerald-400 font-bold">1:{takeProfitRatio}</span>
        </div>

        {/* Trailing Stop */}
        <div className="flex items-center justify-between text-[10px] font-mono">
          <span className="text-zinc-500 flex items-center gap-1">
            <Move size={10} /> Trailing Stop
          </span>
          <span className={useTrailing ? "text-emerald-400" : "text-zinc-500"}>
            {useTrailing ? '✓ Enabled' : '✗ Disabled'}
          </span>
        </div>

        {/* Calculated Levels */}
        {levels && (
          <div className="mt-2 pt-2 border-t border-zinc-700/50 space-y-1.5">
            {entryPrice && (
              <div className="flex items-center justify-between">
                <span className="text-[10px] text-zinc-400">Entry</span>
                <span className="text-xs font-mono text-zinc-200">{entryPrice.toFixed(4)}</span>
              </div>
            )}
            <div className="flex items-center justify-between">
              <span className="text-[10px] text-rose-400">Stop Loss</span>
              <span className="text-xs font-mono text-rose-400 font-bold">{levels.stopLoss.toFixed(4)}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-[10px] text-emerald-400">Take Profit</span>
              <span className="text-xs font-mono text-emerald-400 font-bold">{levels.takeProfit.toFixed(4)}</span>
            </div>
            {levels.trailingStop && (
              <div className="flex items-center justify-between">
                <span className="text-[10px] text-amber-400">Trailing</span>
                <span className="text-xs font-mono text-amber-400">{levels.trailingStop.toFixed(4)}</span>
              </div>
            )}
            {/* Risk:Reward badge */}
            <div className="mt-2 flex justify-center">
              <span className="px-2 py-0.5 bg-zinc-800 rounded text-[10px] text-zinc-300">
                R:R = 1:{riskReward}
              </span>
            </div>
          </div>
        )}
      </div>

      {/* Output handle */}
      <Handle 
        type="source" 
        position={Position.Bottom} 
        className="w-2 h-2 bg-rose-500 border-none !-bottom-1"
      />
    </motion.div>
  );
};

export default RiskManagementNode;
