/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { motion } from 'motion/react';
import { Activity, Check, Globe } from 'lucide-react';

import { cn } from '../../lib/utils';
import type { WorkflowNodeData } from './types';

export const CustomNode = ({ data, selected }: NodeProps) => {
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
