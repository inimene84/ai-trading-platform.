/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */
import React from 'react';
import { Plus } from 'lucide-react';

import { cn } from '../../lib/utils';

export function DraggableComponent({ icon, label, type }: { icon: React.ReactNode, label: string, type: string }) {
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
