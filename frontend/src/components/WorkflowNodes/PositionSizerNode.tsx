import React from 'react';
import { Handle, Position, NodeProps } from '@xyflow/react';
import { Scale, DollarSign, Percent } from 'lucide-react';
import { motion } from 'motion/react';
import { cn } from '../../lib/utils';
import { PositionSizerConfig } from '../../services/workflowEngine';

interface PositionSizerNodeData {
  label: string;
  type: string;
  icon?: any;
  color?: string;
  config?: PositionSizerConfig;
  executing?: boolean;
  calculatedSize?: number;
  equity?: number;
}

export const PositionSizerNode: React.FC<NodeProps<PositionSizerNodeData>> = ({ data, selected }) => {
  const config = data.config;
  const riskPct = config?.riskPerTradePct || 1;
  const useATR = config?.useATR !== false;
  const isExecuting = data.executing;
  const calculatedSize = data.calculatedSize;
  const equity = data.equity || 10000;

  // Calculate potential position
  const riskAmount = equity * (riskPct / 100);

  return (
    <motion.div
      initial={{ scale: 0.9, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      whileHover={{ y: -2 }}
      className={cn(
        "px-4 py-3 rounded-2xl bg-zinc-900/80 backdrop-blur-xl border transition-all min-w-[220px] relative",
        isExecuting ? "border-blue-500/50 shadow-[0_0_25px_rgba(59,130,246,0.3)]" :
        calculatedSize ? "border-cyan-500/50 shadow-[0_0_20px_rgba(6,182,212,0.2)]" :
        selected ? "border-cyan-500/50" : "border-zinc-800/50 hover:border-zinc-700"
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
          "bg-gradient-to-br from-cyan-500/20 to-cyan-600/20"
        )}>
          <div className="absolute inset-0 opacity-30 bg-white" />
          <Scale size={20} className="relative z-10 text-cyan-400" />
          
          {isExecuting && (
            <div className="absolute inset-0 animate-pulse bg-blue-500/20" />
          )}
        </div>

        <div className="flex-1 min-w-0">
          <p className="text-[9px] uppercase text-zinc-500 font-black tracking-[0.1em] leading-none mb-1">
            Position Sizer
          </p>
          <p className="text-xs font-bold text-white leading-none truncate">{data.label}</p>
        </div>
      </div>

      {/* Risk configuration display */}
      <div className="mt-3 pt-2 border-t border-zinc-800/50 space-y-2">
        <div className="flex items-center justify-between text-[10px] font-mono">
          <span className="text-zinc-500 flex items-center gap-1">
            <Percent size={10} /> Risk Per Trade
          </span>
          <span className="text-cyan-400 font-bold">{riskPct}%</span>
        </div>
        <div className="flex items-center justify-between text-[10px] font-mono">
          <span className="text-zinc-500 flex items-center gap-1">
            <DollarSign size={10} /> Risk Amount
          </span>
          <span className="text-zinc-300">${riskAmount.toFixed(2)}</span>
        </div>
        
        {/* ATR indicator */}
        <div className="flex items-center justify-between text-[10px] font-mono">
          <span className="text-zinc-500">Use ATR</span>
          <span className={useATR ? "text-emerald-400" : "text-zinc-500"}>
            {useATR ? '✓ Enabled' : '✗ Disabled'}
          </span>
        </div>

        {/* Calculated size */}
        {calculatedSize && (
          <div className="mt-2 pt-2 border-t border-zinc-700/50">
            <div className="flex items-center justify-between">
              <span className="text-[10px] text-zinc-400">Calculated Size</span>
              <span className="text-sm font-bold text-cyan-400 font-mono">
                {calculatedSize.toFixed(6)}
              </span>
            </div>
          </div>
        )}
      </div>

      {/* Output handle */}
      <Handle 
        type="source" 
        position={Position.Bottom} 
        className="w-2 h-2 bg-cyan-500 border-none !-bottom-1"
      />
    </motion.div>
  );
};

export default PositionSizerNode;
