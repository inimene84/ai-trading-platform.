import React, { useState } from 'react';
import { Play, Square, RefreshCw, CheckCircle2, AlertCircle, Clock, Trash2, Search, Filter } from 'lucide-react';

type TaskStatus = 'running' | 'completed' | 'failed' | 'pending';

interface Task {
  id: string;
  name: string;
  type: string;
  status: TaskStatus;
  startTime?: string;
  duration?: string;
  progress: number;
}

const mockTasks: Task[] = [
  { id: 'tsk_001', name: 'Model Backtesting: Q3 Data', type: 'Backtest', status: 'running', startTime: '10 mins ago', progress: 45 },
  { id: 'tsk_002', name: 'Fetch Binance Historical (BTC/USDT)', type: 'Data Sync', status: 'completed', startTime: '1 hr ago', duration: '12s', progress: 100 },
  { id: 'tsk_003', name: 'Train Agent: Sentiment Analyzer', type: 'Training', status: 'failed', startTime: '2 hrs ago', duration: '45m', progress: 82 },
  { id: 'tsk_004', name: 'Generate Daily Trading Report', type: 'Reporting', status: 'pending', progress: 0 },
  { id: 'tsk_005', name: 'Sync Portfolio Balances', type: 'Data Sync', status: 'completed', startTime: '3 hrs ago', duration: '2s', progress: 100 },
];

export const TaskListView: React.FC = () => {
  const [tasks, setTasks] = useState<Task[]>(mockTasks);
  const [searchTerm, setSearchTerm] = useState('');

  const filteredTasks = tasks.filter(t => t.name.toLowerCase().includes(searchTerm.toLowerCase()) || t.type.toLowerCase().includes(searchTerm.toLowerCase()));

  const getStatusIcon = (status: TaskStatus) => {
    switch (status) {
      case 'running': return <RefreshCw className="w-4 h-4 text-emerald-400 animate-spin" />;
      case 'completed': return <CheckCircle2 className="w-4 h-4 text-blue-400" />;
      case 'failed': return <AlertCircle className="w-4 h-4 text-rose-400" />;
      case 'pending': return <Clock className="w-4 h-4 text-zinc-400" />;
    }
  };

  const getStatusColor = (status: TaskStatus) => {
    switch (status) {
      case 'running': return 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20';
      case 'completed': return 'bg-blue-500/10 text-blue-400 border-blue-500/20';
      case 'failed': return 'bg-rose-500/10 text-rose-400 border-rose-500/20';
      case 'pending': return 'bg-zinc-500/10 text-zinc-400 border-zinc-500/20';
    }
  };

  return (
    <div className="w-full h-full flex flex-col space-y-4 animate-in fade-in zoom-in-95 duration-300">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-white tracking-tight">System Tasks</h2>
          <p className="text-zinc-400 text-sm mt-1">Monitor and manage background operations</p>
        </div>
        <div className="flex items-center space-x-3">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
            <input 
              type="text" 
              placeholder="Search tasks..." 
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="pl-9 pr-4 py-2 bg-zinc-900/50 border border-white/10 rounded-lg text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/50 transition-all w-64"
            />
          </div>
          <button className="p-2 bg-zinc-900/50 border border-white/10 rounded-lg text-zinc-400 hover:text-white hover:border-white/20 transition-colors">
            <Filter className="w-4 h-4" />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-auto bg-zinc-900/40 border border-white/5 rounded-xl backdrop-blur-xl">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="border-b border-white/10 bg-zinc-900/50">
              <th className="px-6 py-4 text-xs font-semibold text-zinc-400 uppercase tracking-wider">Task Name</th>
              <th className="px-6 py-4 text-xs font-semibold text-zinc-400 uppercase tracking-wider">Type</th>
              <th className="px-6 py-4 text-xs font-semibold text-zinc-400 uppercase tracking-wider">Status</th>
              <th className="px-6 py-4 text-xs font-semibold text-zinc-400 uppercase tracking-wider">Progress</th>
              <th className="px-6 py-4 text-xs font-semibold text-zinc-400 uppercase tracking-wider text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {filteredTasks.map((task) => (
              <tr key={task.id} className="group hover:bg-white/[0.02] transition-colors">
                <td className="px-6 py-4 whitespace-nowrap">
                  <div className="flex items-center space-x-3">
                    <div className={`p-2 rounded-lg border ${getStatusColor(task.status)} bg-opacity-20`}>
                      {getStatusIcon(task.status)}
                    </div>
                    <div>
                      <div className="font-medium text-white text-sm">{task.name}</div>
                      <div className="text-xs text-zinc-500 mt-0.5">
                        {task.startTime && <span>Started {task.startTime}</span>}
                        {task.duration && <span className="ml-2">• Duration: {task.duration}</span>}
                      </div>
                    </div>
                  </div>
                </td>
                <td className="px-6 py-4 whitespace-nowrap">
                  <span className="px-2.5 py-1 text-xs font-medium bg-zinc-800 text-zinc-300 rounded-md border border-white/5">
                    {task.type}
                  </span>
                </td>
                <td className="px-6 py-4 whitespace-nowrap">
                  <span className={`px-2.5 py-1 text-xs font-medium rounded-full border flex items-center w-fit space-x-1.5 ${getStatusColor(task.status)}`}>
                    <span className="capitalize">{task.status}</span>
                  </span>
                </td>
                <td className="px-6 py-4 whitespace-nowrap w-48">
                  <div className="flex items-center space-x-3">
                    <div className="flex-1 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                      <div 
                        className={`h-full rounded-full transition-all duration-1000 ${task.status === 'failed' ? 'bg-rose-500' : task.status === 'completed' ? 'bg-blue-500' : 'bg-emerald-500'}`}
                        style={{ width: `${task.progress}%` }}
                      />
                    </div>
                    <span className="text-xs text-zinc-400 font-medium w-8">{task.progress}%</span>
                  </div>
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-right">
                  <div className="flex items-center justify-end space-x-2 opacity-0 group-hover:opacity-100 transition-opacity">
                    {task.status === 'running' ? (
                      <button className="p-1.5 text-zinc-400 hover:text-rose-400 hover:bg-rose-500/10 rounded-md transition-colors" title="Stop Task">
                        <Square className="w-4 h-4 fill-current" />
                      </button>
                    ) : (
                      <button className="p-1.5 text-zinc-400 hover:text-emerald-400 hover:bg-emerald-500/10 rounded-md transition-colors" title="Restart Task">
                        <Play className="w-4 h-4 fill-current" />
                      </button>
                    )}
                    <button className="p-1.5 text-zinc-400 hover:text-rose-400 hover:bg-rose-500/10 rounded-md transition-colors" title="Delete Task">
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {filteredTasks.length === 0 && (
              <tr>
                <td colSpan={5} className="px-6 py-12 text-center text-zinc-500 text-sm">
                  No tasks found matching your search.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
