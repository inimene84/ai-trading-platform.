import React from 'react';
import { StrategyControl } from './StrategyControl';
import { RiskPanel } from './RiskPanel';

export function OperationsPage() {
  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-2xl font-bold tracking-tight">Operations Cockpit</h2>
        <p className="text-sm text-zinc-400">Configure strategies, launch execution loops, and monitor risk parameters</p>
      </div>

      {/* Grid Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <StrategyControl />
        <RiskPanel />
      </div>
    </div>
  );
}
