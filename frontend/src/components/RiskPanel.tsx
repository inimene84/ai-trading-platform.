import React, { useState, useEffect } from 'react';
import { Shield, AlertTriangle, Info } from 'lucide-react';
import { apiService } from '../services/apiService';
import { cn } from '../lib/utils';

export function RiskPanel() {
  const [config, setConfig] = useState<any>(null);
  const [loading, setLoading] = useState<boolean>(true);

  useEffect(() => {
    async function loadConfig() {
      try {
        const cfg = await apiService.getConfig();
        setConfig(cfg);
      } catch (err) {
        console.error('Failed to load risk config:', err);
      } finally {
        setLoading(false);
      }
    }
    loadConfig();
  }, []);

  if (loading) {
    return (
      <div className="bg-[#141416]/60 backdrop-blur-md border border-zinc-800 rounded-2xl p-6 flex items-center justify-center min-h-[300px]">
        <div className="text-center space-y-3">
          <div className="w-8 h-8 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin mx-auto" />
          <p className="text-sm text-zinc-500">Loading risk parameters…</p>
        </div>
      </div>
    );
  }

  const mode = config?.mode || 'unknown';
  const limits = config?.risk_limits || {};

  const getHumanFriendlyLabel = (key: string) => {
    const labels: Record<string, string> = {
      max_positions: 'Max Open Positions',
      max_directional_exposure_usdt: 'Max Directional Exposure',
      trade_usdt_amount: 'Allocation Per Trade',
      kill_floor_usdt: 'System Kill Floor',
      min_signal_strength: 'Min Signal Strength',
      sl_cooldown_minutes: 'Stop Loss Cooldown',
      emergency_drawdown_pct: 'Emergency Drawdown Limit',
    };
    return labels[key] || key.replace(/_/g, ' ');
  };

  const getFormattedValue = (key: string, value: any) => {
    if (key === 'min_signal_strength') {
      return `${(parseFloat(value) * 100).toFixed(0)}%`;
    }
    if (key === 'emergency_drawdown_pct') {
      return `${value}%`;
    }
    if (key.includes('usdt') || key.includes('exposure') || key.includes('amount') || key.includes('floor')) {
      return `$${parseFloat(value).toLocaleString()}`;
    }
    if (key.includes('minutes') || key.includes('cooldown')) {
      return `${value} min`;
    }
    return String(value);
  };

  const isLive = mode === 'live';

  return (
    <div className="bg-[#141416]/60 backdrop-blur-md border border-zinc-800 rounded-2xl p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className={cn(
            "p-2.5 rounded-xl flex items-center justify-center",
            isLive ? "bg-amber-500/10 text-amber-400" : "bg-emerald-500/10 text-emerald-400"
          )}>
            <Shield size={20} />
          </div>
          <div>
            <h3 className="font-bold text-base flex items-center gap-2">
              Risk & Capital Limits
              <span className={cn(
                "inline-block px-2.5 py-0.5 text-[9px] font-bold uppercase tracking-wider rounded-full border",
                isLive
                  ? "bg-amber-500/10 border-amber-500/20 text-amber-400"
                  : "bg-emerald-500/10 border-emerald-500/20 text-emerald-400"
              )}>
                {mode} Mode
              </span>
            </h3>
            <p className="text-xs text-zinc-500 mt-0.5">Guardrails protecting capital safety</p>
          </div>
        </div>
      </div>

      {/* Grid of Limits */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {Object.entries(limits).map(([key, value]) => (
          <div key={key} className="bg-zinc-900/40 p-4 rounded-xl border border-zinc-80 border-zinc-800/80 flex justify-between items-center transition-all hover:bg-zinc-900/60">
            <div>
              <span className="text-zinc-400 text-xs block font-semibold">{getHumanFriendlyLabel(key)}</span>
              <span className="text-[9px] text-zinc-600 font-mono block mt-0.5">{key}</span>
            </div>
            <span className="font-mono font-bold text-sm text-zinc-200">
              {getFormattedValue(key, value)}
            </span>
          </div>
        ))}
      </div>

      {/* Warnings & Advice */}
      <div className="flex items-start gap-3 bg-zinc-900/30 border border-zinc-800 p-4 rounded-xl text-zinc-400 text-xs">
        <Info size={16} className="text-emerald-400 mt-0.5 flex-shrink-0" />
        <div className="space-y-1 text-zinc-500">
          <p>
            To adjust risk configurations, edit your local config variables inside the <code className="font-mono bg-zinc-900 text-zinc-400 px-1 py-0.5 rounded text-[10px] border border-zinc-800">.env</code> file on the backend server.
          </p>
          {isLive && (
            <p className="text-amber-500/80 font-semibold flex items-center gap-1 mt-1">
              <AlertTriangle size={12} /> Live mode is active. Verify all limits twice to prevent large drawdowns.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
