import React from 'react';
import { Handle, Position, Node, NodeProps } from '@xyflow/react';
import { Power, AlertTriangle, Lock, Unlock } from 'lucide-react';
import { motion } from 'motion/react';
import { cn } from '../../lib/utils';
import { KillswitchConfig } from '../../services/workflowEngine';

interface KillswitchNodeData extends Record<string, unknown> {
  label: string;
  type: string;
  icon?: any;
  color?: string;
  config?: KillswitchConfig;
  executing?: boolean;
  isActive?: boolean;
  activeUntil?: number;
  triggered?: boolean;
  pnlData?: {
    dailyPnL: number;
    maxLossPct: number;
    currentLossPct: number;
  };
}

type KillswitchNodeType = Node<KillswitchNodeData>;

export const KillswitchNode: React.FC<NodeProps<KillswitchNodeType>> = ({ data, selected }) => {
  const config = data.config;
  const isExecuting = data.executing;
  const isActive = config?.enabled !== false;
  const triggered = data.triggered;
  const maxLoss = config?.maxDailyLossPct || 5;
  const haltHours = config?.haltDurationHours || 4;
  const pnlData = data.pnlData;

  const activeUntil = data.activeUntil;
  const timeRemaining = activeUntil ? Math.max(0, Math.ceil((activeUntil - Date.now()) / 60000)) : 0; // minutes

  return (
    <motion.div
      initial={{ scale: 0.9, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      whileHover={{ y: -2 }}
      className={cn(
        "px-4 py-3 rounded-2xl border transition-all min-w-[240px] relative",
        triggered 
          ? "bg-red-950/80 border-red-500 shadow-[0_0_30px_rgba(239,68,68,0.4)]" 
          : isExecuting 
            ? "bg-zinc-900/80 border-blue-500/50 shadow-[0_0_25px_rgba(59,130,246,0.3)]"
            : selected 
              ? "bg-zinc-900/80 border-red-500/50" 
              : "bg-zinc-900/80 border-zinc-800/50 hover:border-zinc-700"
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
          triggered 
            ? "bg-gradient-to-br from-red-500/30 to-red-600/30" 
            : "bg-gradient-to-br from-red-500/20 to-red-600/20"
        )}>
          <div className="absolute inset-0 opacity-30 bg-white" />
          {triggered ? (
            <AlertTriangle size={20} className="relative z-10 text-red-400 animate-pulse" />
          ) : (
            <Power size={20} className={cn("relative z-10", isActive ? "text-red-400" : "text-zinc-500")} />
          )}
          
          {isExecuting && (
            <div className="absolute inset-0 animate-pulse bg-blue-500/20" />
          )}
          {triggered && (
            <div className="absolute inset-0 animate-pulse bg-red-500/30" />
          )}
        </div>

        <div className="flex-1 min-w-0">
          <p className="text-[9px] uppercase text-zinc-500 font-black tracking-[0.1em] leading-none mb-1">
            {triggered ? '⚠ CIRCUIT BREAKER' : 'Killswitch'}
          </p>
          <p className="text-xs font-bold text-white leading-none truncate">{data.label}</p>
        </div>

        {/* Status indicator */}
        {triggered ? (
          <motion.div 
            animate={{ scale: [1, 1.2, 1] }}
            transition={{ repeat: Infinity, duration: 1 }}
            className="w-8 h-8 rounded-full bg-red-500/20 flex items-center justify-center"
          >
            <Lock size={16} className="text-red-400" />
          </motion.div>
        ) : isActive ? (
          <div className="w-8 h-8 rounded-full bg-emerald-500/20 flex items-center justify-center">
            <Unlock size={16} className="text-emerald-400" />
          </div>
        ) : null}
      </div>

      {/* Configuration */}
      <div className="mt-3 pt-2 border-t border-zinc-800/50 space-y-2">
        {/* Max Loss Limit */}
        <div className="flex items-center justify-between text-[10px] font-mono">
          <span className="text-zinc-500">Daily Loss Limit</span>
          <span className={triggered ? "text-red-400 font-bold" : "text-amber-400 font-bold"}>
            {maxLoss}%
          </span>
        </div>

        {/* Halt Duration */}
        <div className="flex items-center justify-between text-[10px] font-mono">
          <span className="text-zinc-500">Halt Duration</span>
          <span className="text-zinc-400">{haltHours}h</span>
        </div>

        {/* Status */}
        <div className="flex items-center justify-between text-[10px] font-mono">
          <span className="text-zinc-500">Status</span>
          <span className={triggered ? "text-red-400 font-bold animate-pulse" : isActive ? "text-emerald-400" : "text-zinc-500"}>
            {triggered ? '🔒 HALTED' : isActive ? '✓ ARMED' : '○ DISARMED'}
          </span>
        </div>

        {/* PnL Display */}
        {pnlData && (
          <div className="mt-2 pt-2 border-t border-zinc-700/50">
            <div className="flex items-center justify-between text-[10px] font-mono">
              <span className="text-zinc-400">Daily PnL</span>
              <span className={pnlData.dailyPnL >= 0 ? "text-emerald-400" : "text-rose-400"}>
                ${pnlData.dailyPnL.toFixed(2)}
              </span>
            </div>
            <div className="flex items-center justify-between text-[10px] font-mono mt-1">
              <span className="text-zinc-400">Current Loss</span>
              <span className={pnlData.currentLossPct >= maxLoss ? "text-red-400 font-bold" : "text-zinc-300"}>
                {pnlData.currentLossPct.toFixed(2)}%
              </span>
            </div>
            {/* Progress bar */}
            <div className="mt-2 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
              <div 
                className={cn(
                  "h-full transition-all duration-500",
                  pnlData.currentLossPct >= maxLoss ? "bg-red-500" :
                  pnlData.currentLossPct >= maxLoss * 0.8 ? "bg-amber-500" :
                  "bg-emerald-500"
                )}
                style={{ width: `${Math.min(100, (pnlData.currentLossPct / maxLoss) * 100)}%` }}
              />
            </div>
          </div>
        )}

        {/* Time remaining if halted */}
        {triggered && timeRemaining > 0 && (
          <div className="mt-2 pt-2 border-t border-red-900/50 text-center">
            <p className="text-[10px] text-red-400">
              Resumes in {timeRemaining} minute{timeRemaining > 1 ? 's' : ''}
            </p>
          </div>
        )}
      </div>

      {/* Output handle - only active if not triggered */}
      <Handle 
        type="source" 
        position={Position.Bottom}
        className={cn(
          "w-2 h-2 border-none !-bottom-1",
          triggered ? "bg-red-500 opacity-30" : "bg-red-500"
        )}
      />
    </motion.div>
  );
};

export default KillswitchNode;
