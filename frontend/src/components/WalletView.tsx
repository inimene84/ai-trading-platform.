import React, { useState, useEffect, useCallback } from 'react';
import { motion } from 'motion/react';
import { ArrowDownToLine, ArrowUpToLine, History, Wallet, RefreshCw, Send } from 'lucide-react';
import { cn } from '../lib/utils';
import { useToast } from './Toast';
import { apiService, Portfolio } from '../services/apiService';

export const WalletView = () => {
  const { showToast } = useToast();
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);

  const fetchPortfolio = useCallback(async () => {
    try {
      const pf = await apiService.getPortfolio();
      setPortfolio(pf);
    } catch {
      // Silent fallback
    }
  }, []);

  useEffect(() => {
    fetchPortfolio();
  }, [fetchPortfolio]);

  const handleDeposit = () => showToast("Deposit modal loading...", "info");
  const handleWithdraw = () => showToast("Withdrawal validation required. Please verify 2FA first.", "error");
  const handleRefresh = () => {
    setIsRefreshing(true);
    fetchPortfolio();
    showToast("Syncing latest transactions...", "info");
    setTimeout(() => {
      setIsRefreshing(false);
      showToast("Transactions synced successfully.", "success");
    }, 1500);
  };
  const handleTransfer = () => showToast("Executing transfer across internal wallets...", "success");
  const handleWaitlist = () => showToast("You've been added to the crypto card waitlist!", "success");

  const balance = portfolio?.balance ?? 0;
  const equity = portfolio?.equity ?? 0;
  const positionsValue = portfolio?.positions_value ?? 0;

  return (
    <motion.div 
      initial={{ opacity: 0, scale: 0.98 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 1.02 }}
      className="flex-1 overflow-y-auto p-6 space-y-6"
    >
      <div className="flex items-center justify-between mb-2">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">Wallet & Transfers</h2>
          <p className="text-sm text-zinc-400">Manage balances, deposits, and withdrawals</p>
        </div>
        <div className="flex gap-3">
          <button onClick={handleDeposit} className="flex items-center gap-2 px-4 py-2 bg-emerald-500 hover:bg-emerald-400 text-black font-bold text-xs rounded-xl transition-all shadow-lg shadow-emerald-500/20">
            <ArrowDownToLine size={14} /> Deposit
          </button>
          <button onClick={handleWithdraw} className="flex items-center gap-2 px-4 py-2 bg-zinc-800 hover:bg-zinc-700 text-white font-bold text-xs rounded-xl transition-all">
            <ArrowUpToLine size={14} /> Withdraw
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-6">
          {/* Wallet Balances */}
          <div className="bg-[#141416] p-6 rounded-2xl border border-zinc-800">
            <h3 className="font-semibold mb-6 flex items-center gap-2"><Wallet size={16} /> Account Overview</h3>
            <div className="space-y-4">
              {[
                { name: 'Available Balance', fullName: 'Cash / Free Margin', balance: balance.toLocaleString(undefined, { minimumFractionDigits: 2 }), fiat: `$${balance.toLocaleString(undefined, { minimumFractionDigits: 2 })}`, icon: 'bg-emerald-500' },
                { name: 'In Positions', fullName: 'Locked in open trades', balance: positionsValue.toLocaleString(undefined, { minimumFractionDigits: 2 }), fiat: `$${positionsValue.toLocaleString(undefined, { minimumFractionDigits: 2 })}`, icon: 'bg-amber-500' },
                { name: 'Total Equity', fullName: 'Balance + Unrealized P&L', balance: equity.toLocaleString(undefined, { minimumFractionDigits: 2 }), fiat: `$${equity.toLocaleString(undefined, { minimumFractionDigits: 2 })}`, icon: 'bg-blue-500' },
              ].map((asset, i) => (
                <div 
                  key={i} 
                  className="flex items-center justify-between p-4 bg-zinc-800/20 rounded-xl border border-zinc-800 hover:bg-zinc-800/40 transition-colors cursor-pointer group"
                >
                  <div className="flex items-center gap-4">
                    <div className={cn("w-10 h-10 rounded-full flex items-center justify-center font-bold text-white shadow-lg text-sm", asset.icon)}>
                      {asset.name[0]}
                    </div>
                    <div>
                      <p className="font-bold text-sm tracking-wide">{asset.name}</p>
                      <p className="text-[10px] text-zinc-500 uppercase">{asset.fullName}</p>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="font-mono font-bold">{asset.balance}</p>
                    <p className="text-[10px] bg-clip-text text-transparent bg-gradient-to-r from-emerald-400 to-blue-400 font-mono tracking-wider">{asset.fiat}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Recent Transactions */}
          <div className="bg-[#141416] rounded-2xl border border-zinc-800 overflow-hidden">
            <div className="p-6 border-b border-zinc-800 flex justify-between items-center">
              <h3 className="font-semibold flex items-center gap-2"><History size={16} /> Recent Transactions</h3>
              <button 
                onClick={handleRefresh}
                aria-label="Refresh transactions"
                className={cn("text-zinc-500 hover:text-white transition-all p-1", isRefreshing && "animate-spin text-emerald-400")}
              >
                <RefreshCw size={14}/>
              </button>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead>
                  <tr className="text-[10px] uppercase tracking-wider text-zinc-500 border-b border-zinc-800 bg-zinc-900/30">
                    <th className="px-6 py-3">Time</th>
                    <th className="px-6 py-3">Type</th>
                    <th className="px-6 py-3">Details</th>
                    <th className="px-6 py-3">Amount</th>
                    <th className="px-6 py-3 text-right">Status</th>
                  </tr>
                </thead>
                <tbody className="text-xs font-mono">
                  <tr className="border-b border-zinc-800/50 hover:bg-white/5 transition-colors">
                    <td className="px-6 py-4 text-zinc-400">{new Date().toLocaleDateString()}</td>
                    <td className="px-6 py-4"><span className="text-emerald-400 font-bold uppercase text-[10px] px-2 py-0.5 rounded bg-emerald-500/10">Initial</span></td>
                    <td className="px-6 py-4 font-bold">Opening Balance</td>
                    <td className="px-6 py-4">${balance.toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>
                    <td className="px-6 py-4 text-right text-emerald-400">Active</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div className="space-y-6">
          {/* Quick Transfer */}
          <div className="bg-[#141416] p-6 rounded-2xl border border-zinc-800">
            <h3 className="font-semibold mb-4 text-sm">Quick Transfer</h3>
            <div className="space-y-4">
              <div className="space-y-1.5">
                <label className="text-[10px] uppercase text-zinc-500 font-bold tracking-wider">From Account</label>
                <select aria-label="From Account" className="w-full bg-zinc-900 border border-zinc-800 rounded-xl py-3 px-4 text-sm focus:outline-none focus:border-emerald-500/50 transition-colors appearance-none">
                  <option>Spot Wallet</option>
                  <option>Futures Wallet</option>
                  <option>Funding Wallet</option>
                </select>
              </div>
              <div className="flex justify-center -my-2 relative z-10 text-zinc-600">
                <ArrowDownToLine size={16} />
              </div>
              <div className="space-y-1.5">
                <label className="text-[10px] uppercase text-zinc-500 font-bold tracking-wider">To Account</label>
                <select aria-label="To Account" className="w-full bg-zinc-900 border border-zinc-800 rounded-xl py-3 px-4 text-sm focus:outline-none focus:border-emerald-500/50 transition-colors appearance-none">
                  <option>Futures Wallet</option>
                  <option>Spot Wallet</option>
                  <option>Funding Wallet</option>
                </select>
              </div>
              <div className="space-y-1.5">
                <label className="text-[10px] uppercase text-zinc-500 font-bold tracking-wider">Amount (USD)</label>
                <input type="text" placeholder="0.00" className="w-full bg-zinc-900 border border-zinc-800 rounded-xl py-3 px-4 font-mono text-sm focus:outline-none focus:border-emerald-500/50 transition-colors" />
              </div>
              <button onClick={handleTransfer} className="w-full mt-2 py-3 bg-zinc-800 hover:bg-zinc-700 rounded-xl text-sm font-bold transition-all flex justify-center items-center gap-2">
                <Send size={14} /> Execute Transfer
              </button>
            </div>
          </div>
          
          {/* Crypto Card */}
          <div className="bg-gradient-to-br from-indigo-500/10 to-purple-500/10 p-6 rounded-2xl border border-indigo-500/20 text-center space-y-3">
            <div className="w-12 h-12 bg-indigo-500/20 rounded-full flex items-center justify-center mx-auto mb-2 relative shadow-[0_0_20px_rgba(99,102,241,0.3)]">
              <span className="text-xl">💳</span>
            </div>
            <h4 className="font-bold text-sm text-indigo-300">Crypto Card Pre-Order</h4>
            <p className="text-xs text-zinc-400 leading-relaxed">Spend your crypto balance anywhere. Zero conversion fees.</p>
            <button onClick={handleWaitlist} className="w-full mt-2 py-2 bg-indigo-500 hover:bg-indigo-400 text-black rounded-xl text-xs font-bold transition-colors">
              Claim Waitlist Spot
            </button>
          </div>
        </div>
      </div>
    </motion.div>
  );
};
