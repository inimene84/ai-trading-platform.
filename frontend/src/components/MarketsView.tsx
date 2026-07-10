import React, { useState, useEffect } from 'react';
import { motion } from 'motion/react';
import { Search, TrendingUp, Star, RefreshCw } from 'lucide-react';
import { cn } from '../lib/utils';
import { useToast } from './Toast';
import { apiService } from '../services/apiService';
import { fetchBinance } from '../services/binanceProxy';
interface CoinData {
  symbol: string;
  baseAsset: string;
  quoteAsset: string;
  price: string;
  change: string;
  volume: string;
  up: boolean;
}

export const MarketsView = () => {
  const { showToast } = useToast();
  const [activeTab, setActiveTab] = useState<'crypto' | 'stocks' | 'forex'>('crypto');
  const [search, setSearch] = useState('');
  const [coins, setCoins] = useState<CoinData[]>([]);
  const [loading, setLoading] = useState(true);
  const [watchlist, setWatchlist] = useState<Set<string>>(new Set());

  const fetchPrices = async () => {
    setLoading(true);
    try {
      if (activeTab === 'crypto') {
        // Fetch top trading pairs from Binance (backend proxy fallback)
        const resp = await fetchBinance('https://api.binance.com/api/v3/ticker/24hr');
        const data = await resp.json();

        // Filter USDT pairs and sort by volume
        const usdtPairs: CoinData[] = data
          .filter((t: any) => (t.symbol.endsWith('USDT') || t.symbol.endsWith('USDC')) && parseFloat(t.quoteVolume) > 1_000_000)
          .map((t: any) => ({
            symbol: t.symbol,
            baseAsset: t.symbol.replace('USDT', ''),
            quoteAsset: 'USDT',
            price: parseFloat(t.lastPrice).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 6 }),
            change: `${parseFloat(t.priceChangePercent) >= 0 ? '+' : ''}${parseFloat(t.priceChangePercent).toFixed(2)}%`,
            volume: `$${(parseFloat(t.quoteVolume) / 1e6).toFixed(1)}M`,
            up: parseFloat(t.priceChangePercent) >= 0,
          }))
          .sort((a: any, b: any) => parseFloat(b.volume.replace(/[^0-9.]/g, '')) - parseFloat(a.volume.replace(/[^0-9.]/g, '')))
          .slice(0, 30);

        setCoins(usdtPairs);
      } else if (activeTab === 'stocks') {
        const resp = await apiService.getStocks();
        const data: CoinData[] = resp.data.map((t: any) => ({
          symbol: t.symbol,
          baseAsset: t.symbol,
          quoteAsset: 'USD',
          price: t.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
          change: `${t.change24h >= 0 ? '+' : ''}${t.change24h.toFixed(2)}%`,
          volume: t.volume24h ? `${(t.volume24h / 1e6).toFixed(1)}M` : 'N/A',
          up: t.up,
        }));
        setCoins(data);
      } else if (activeTab === 'forex') {
        const resp = await apiService.getForex();
        const data: CoinData[] = resp.data.map((t: any) => ({
          symbol: t.symbol,
          baseAsset: t.symbol.split('/')[0],
          quoteAsset: t.symbol.split('/')[1] || 'USD',
          price: t.price.toFixed(4),
          change: `${t.change24h >= 0 ? '+' : ''}${t.change24h.toFixed(3)}%`,
          volume: 'N/A',
          up: t.up,
        }));
        setCoins(data);
      }
    } catch (err) {
      console.warn(`Failed to fetch ${activeTab} prices:`, err);
      setCoins([]);
      showToast(`${activeTab} market data unavailable`, 'error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPrices();
    const iv = setInterval(fetchPrices, 30_000); // refresh every 30 seconds
    return () => clearInterval(iv);
  }, [activeTab]);

  const handleTrade = (asset: string) => {
    showToast(`Redirecting to trade terminal for ${asset}...`, 'success');
  };

  const handleWatchlist = (asset: string) => {
    setWatchlist(prev => {
      const next = new Set(prev);
      if (next.has(asset)) {
        next.delete(asset);
        showToast(`${asset} removed from watchlist.`, 'info');
      } else {
        next.add(asset);
        showToast(`${asset} added to watchlist.`, 'success');
      }
      return next;
    });
  };

  const filteredCoins = coins.filter(c =>
    c.baseAsset.toLowerCase().includes(search.toLowerCase()) ||
    c.symbol.toLowerCase().includes(search.toLowerCase())
  );

  // Top movers
  const topMovers = [...coins].sort((a, b) =>
    Math.abs(parseFloat(b.change)) - Math.abs(parseFloat(a.change))
  ).slice(0, 4);

  return (
    <motion.div 
      initial={{ opacity: 0, scale: 0.98 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 1.02 }}
      className="flex-1 overflow-y-auto p-6 flex flex-col gap-6"
    >
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">Markets Overview</h2>
          <p className="text-sm text-zinc-400">Live prices from Binance · Auto-refreshes every 30s</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" size={16} />
            <input 
              type="text" 
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search coins..." 
              className="w-64 bg-zinc-900 border border-zinc-800 rounded-xl py-2 pl-10 pr-4 text-sm focus:outline-none focus:border-emerald-500/50"
            />
          </div>
          <button
            onClick={fetchPrices}
            className="flex items-center gap-2 px-4 py-2 bg-zinc-800 hover:bg-zinc-700 rounded-xl text-xs font-bold transition-colors"
          >
            <RefreshCw size={14} className={cn(loading && "animate-spin")} /> Refresh
          </button>
        </div>
      </div>

      {/* Top Movers */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {topMovers.map(asset => (
          <div 
            key={asset.symbol} 
            onClick={() => handleTrade(asset.baseAsset)}
            className="bg-[#141416] p-4 rounded-xl border border-zinc-800 flex justify-between items-center group cursor-pointer hover:border-emerald-500/30 hover:bg-emerald-500/5 transition-all"
          >
            <div>
              <p className="font-bold text-sm tracking-wide">{asset.baseAsset}/{asset.quoteAsset}</p>
              <p className="text-xs text-zinc-500 flex items-center gap-1 mt-1">Vol: {asset.volume}</p>
            </div>
            <div className="text-right">
              <p className="font-mono text-sm font-bold">{asset.price}</p>
              <p className={cn("text-[10px] font-mono tracking-wider items-center flex gap-1 justify-end", asset.up ? "text-emerald-400" : "text-rose-400")}>
                {asset.up ? <TrendingUp size={10} /> : <TrendingUp size={10} className="rotate-180" />}
                {asset.change}
              </p>
            </div>
          </div>
        ))}
      </div>

      {/* Main Table */}
      <div className="flex-1 bg-[#141416] border border-zinc-800 rounded-2xl overflow-hidden flex flex-col">
        <div className="p-6 border-b border-zinc-800 flex items-center gap-4">
          <button 
            onClick={() => setActiveTab('crypto')}
            className={cn("px-4 py-2 rounded-lg text-sm font-bold transition-colors", activeTab === 'crypto' ? "bg-zinc-800 text-white" : "text-zinc-500 hover:text-white")}
          >
            Cryptocurrency
          </button>
          <button 
            onClick={() => setActiveTab('stocks')}
            className={cn("px-4 py-2 rounded-lg text-sm font-bold transition-colors", activeTab === 'stocks' ? "bg-zinc-800 text-white" : "text-zinc-500 hover:text-white")}
          >
            Stocks
          </button>
          <button 
            onClick={() => setActiveTab('forex')}
            className={cn("px-4 py-2 rounded-lg text-sm font-bold transition-colors", activeTab === 'forex' ? "bg-zinc-800 text-white" : "text-zinc-500 hover:text-white")}
          >
            Forex
          </button>
          {loading && <div className="w-4 h-4 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin ml-2" />}
        </div>
        <div className="flex-1 overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="text-[10px] uppercase tracking-wider text-zinc-500 border-b border-zinc-800">
                <th className="px-6 py-4 w-10"></th>
                <th className="px-6 py-4 text-xs font-bold">#</th>
                <th className="px-6 py-4 text-xs font-bold">Asset</th>
                <th className="px-6 py-4 text-xs font-bold">Price</th>
                <th className="px-6 py-4 text-xs font-bold">24h Change</th>
                <th className="px-6 py-4 text-xs font-bold">24h Volume</th>
                <th className="px-6 py-4 text-right">Action</th>
              </tr>
            </thead>
            <tbody className="text-sm">
              {filteredCoins.map((coin, i) => (
                <tr key={coin.symbol} className="border-b border-zinc-800/50 hover:bg-white/5 transition-colors group">
                  <td
                    onClick={() => handleWatchlist(coin.baseAsset)}
                    className={cn("px-6 py-4 cursor-pointer transition-colors", watchlist.has(coin.baseAsset) ? "text-amber-400" : "text-zinc-500 hover:text-amber-400")}
                  >
                    <Star size={16} className={cn(watchlist.has(coin.baseAsset) && "fill-current")} />
                  </td>
                  <td className="px-6 py-4 text-zinc-500 font-mono text-xs">{i + 1}</td>
                  <td className="px-6 py-4">
                    <span className="font-bold block">{coin.baseAsset}</span>
                    <span className="text-[10px] text-zinc-500 uppercase">{coin.baseAsset}/{coin.quoteAsset}</span>
                  </td>
                  <td className="px-6 py-4 font-mono">
                    {activeTab === 'forex' ? '' : '$'}{coin.price}
                  </td>
                  <td className={cn("px-6 py-4 font-mono font-bold", coin.up ? "text-emerald-400" : "text-rose-400")}>
                    {coin.change}
                  </td>
                  <td className="px-6 py-4 text-zinc-400 font-mono tracking-wider">{coin.volume}</td>
                  <td className="px-6 py-4 text-right">
                    <button onClick={() => handleTrade(coin.baseAsset)} className="text-xs bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 px-4 py-2 rounded-lg font-bold transition-colors border border-emerald-500/20">
                      Trade
                    </button>
                  </td>
                </tr>
              ))}
              {filteredCoins.length === 0 && !loading && (
                <tr>
                  <td colSpan={7} className="text-center py-12 text-zinc-500 font-mono">
                    Nothing to display right now
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </motion.div>
  );
};
