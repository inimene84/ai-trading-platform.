import React from 'react';
import { Handle, Position, NodeProps } from '@xyflow/react';
import { Activity, TrendingUp, Clock, BarChart3 } from 'lucide-react';
import { motion } from 'motion/react';
import { cn } from '../../lib/utils';
import { FilterConfig } from '../../services/workflowEngine';

interface FilterNodeData {
  label: string;
  type: string;
  icon?: any;
  color?: string;
  config?: FilterConfig;
  executing?: boolean;
  lastResult?: boolean;
  lastValue?: number;
}

const indicatorIcons: Record<string, any> = {
  rsi: Activity,
  ema: TrendingUp,
  time: Clock,
  volume: BarChart3
};

export const FilterNode: React.FC<NodeProps<FilterNodeData>> = ({ data, selected }) => {
  const config = data.config;
  const indicator = config?.indicator || 'rsi';
  const Icon = indicatorIcons[indicator] || Activity;
  const isExecuting = data.executing;
  const lastResult = data.lastResult;
  const lastValue = data.lastValue;

  // Determine status color
  let statusColor = 'amber';
  if (isExecuting) statusColor = 'blue';
  else if (lastResult === true) statusColor = 'emerald';
  else if (lastResult === false) statusColor = 'rose';

  return (
    <motion.div
      initial={{ scale: 0.9, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      whileHover={{ y: -2 }}
      className={cn(
        "px-4 py-3 rounded-2xl bg-zinc-900/80 backdrop-blur-xl border transition-all min-w-[220px] relative",
        isExecuting ? "border-blue-500/50 shadow-[0_0_25px_rgba(59,130,246,0.3)]" :
        lastResult === true ? "border-emerald-500/50 shadow-[0_0_20px_rgba(16,185,129,0.2)]" :
        lastResult === false ? "border-rose-500/50 shadow-[0_0_20px_rgba(244,63,94,0.2)]" :
        selected ? "border-amber-500/50" : "border-zinc-800/50 hover:border-zinc-700"
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
          "bg-gradient-to-br",
          indicator === 'rsi' ? "from-purple-500/20 to-purple-600/20" :
          indicator === 'ema' ? "from-cyan-500/20 to-cyan-600/20" :
          indicator === 'volume' ? "from-orange-500/20 to-orange-600/20" :
          "from-amber-500/20 to-amber-600/20"
        )}>
          <div className="absolute inset-0 opacity-30 bg-white" />
          <Icon size={20} className={cn(
            "relative z-10",
            indicator === 'rsi' ? "text-purple-400" :
            indicator === 'ema' ? "text-cyan-400" :
            indicator === 'volume' ? "text-orange-400" :
            "text-amber-400"
          )} />
          
          {/* Execution pulse */}
          {isExecuting && (
            <div className="absolute inset-0 animate-pulse bg-blue-500/20" />
          )}
        </div>

        <div className="flex-1 min-w-0">
          <p className="text-[9px] uppercase text-zinc-500 font-black tracking-[0.1em] leading-none mb-1">
            {indicator.toUpperCase()} Filter
          </p>
          <p className="text-xs font-bold text-white leading-none truncate">{data.label}</p>
        </div>

        {/* Status indicator */}
        {lastResult !== undefined && !isExecuting && (
          <div className={cn(
            "w-6 h-6 rounded-full flex items-center justify-center",
            lastResult ? "bg-emerald-500/20 text-emerald-400" : "bg-rose-500/20 text-rose-400"
          )}>
            {lastResult ? '✓' : '✗'}
          </div>
        )}
      </div>

      {/* Configuration display */}
      <div className="mt-3 pt-2 border-t border-zinc-800/50">
        <div className="flex items-center justify-between text-[10px] font-mono">
          <span className="text-zinc-500">
            {config?.period || '14'} periods
          </span>
          <span className={cn(
            "px-2 py-0.5 rounded bg-zinc-800",
            lastValue !== undefined ? (
              lastResult ? "text-emerald-400" : "text-rose-400"
            ) : "text-zinc-400"
          )}>
            {lastValue !== undefined ? `${lastValue.toFixed(1)}` : '--'}
            {config?.operator} {config?.threshold || 30}
          </span>
        </div>
      </div>

      {/* Pass/Fail output handles */}
      <Handle 
        type="source" 
        position={Position.Bottom} 
        id="pass"
        className="w-2 h-2 bg-emerald-500 border-none !-bottom-1 !left-[30%]"
        style={{ left: '30%' }}
      />
      <Handle 
        type="source" 
        position={Position.Bottom} 
        id="fail"
        className="w-2 h-2 bg-rose-500 border-none !-bottom-1 !left-[70%]"
        style={{ left: '70%' }}
      />

      {/* Labels for handles */}
      <div className="absolute -bottom-5 left-[30%] -translate-x-1/2 text-[8px] text-emerald-400 font-medium">
        Pass
      </div>
      <div className="absolute -bottom-5 left-[70%] -translate-x-1/2 text-[8px] text-rose-400 font-medium">
        Fail
      </div>
    </motion.div>
  );
};

export default FilterNode;
