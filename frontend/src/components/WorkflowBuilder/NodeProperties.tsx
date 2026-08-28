/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */
import React, { useState, useEffect } from 'react';
import type { Node as FlowNode } from '@xyflow/react';

import { cn } from '../../lib/utils';
import type { WorkflowNodeData } from './types';

export const NodeProperties = ({ node, onUpdate }: { node: FlowNode, onUpdate: (id: string, config: any) => void }) => {
  const nodeData = node.data as unknown as WorkflowNodeData;
  const label = nodeData.label.toLowerCase();

  const [config, setConfig] = useState<any>(nodeData.config || {});

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
        <div className={cn("p-1.5 rounded-lg", nodeData.color)}>
          {React.createElement(nodeData.icon as any, { size: 14, className: "text-white" })}
        </div>
        <div>
          <h3 className="text-xs font-bold text-white">{nodeData.label}</h3>
          <p className="text-[9px] text-zinc-500 uppercase font-bold tracking-widest">{nodeData.type} Configuration</p>
        </div>
      </div>
      {renderConfigFields()}
    </div>
  );
};
