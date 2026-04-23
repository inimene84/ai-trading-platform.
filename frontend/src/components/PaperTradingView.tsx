import { useEffect, useState } from 'react';
import { apiService } from '../services/apiService';

interface PaperStats {
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  total_pnl: number;
  total_fees: number;
  cash: number;
  initial_balance: number;
}

interface PaperPosition {
  symbol: string;
  side: string;
  quantity: number;
  avg_price: number;
}

interface PaperOrder {
  id: string;
  symbol: string;
  side: string;
  order_type: string;
  quantity: number;
  price: number;
  status: string;
  filled_qty: number;
  avg_price: number;
}

export default function PaperTradingView() {
  const [stats, setStats] = useState<PaperStats | null>(null);
  const [positions, setPositions] = useState<PaperPosition[]>([]);
  const [orders, setOrders] = useState<PaperOrder[]>([]);
  const [symbol, setSymbol] = useState('BTCUSDT');
  const [qty, setQty] = useState('0.01');
  const [price, setPrice] = useState('');
  const [side, setSide] = useState<'buy' | 'sell'>('buy');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function refresh() {
    try {
      const [s, p, o] = await Promise.all([
        apiService.paperStats(),
        apiService.paperPositions(),
        apiService.paperOrders(),
      ]);
      setStats(s);
      setPositions(p.positions || p || []);
      setOrders(o.orders || o || []);
    } catch (e: any) {
      setError(e.message || 'Failed to load paper data');
    }
  }

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, []);

  async function placeOrder() {
    setLoading(true);
    setError('');
    try {
      await apiService.paperPlaceOrder(
        symbol.toUpperCase(),
        side,
        parseFloat(qty),
        price ? parseFloat(price) : 0
      );
      await refresh();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function cancelOrder(oid: string) {
    try {
      await apiService.paperCancelOrder(oid);
      await refresh();
    } catch (e: any) {
      setError(e.message);
    }
  }

  return (
    <div className="space-y-4">
      {error && (
        <div className="bg-red-900/50 border border-red-500 text-red-200 px-4 py-2 rounded text-sm">
          {error}
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-slate-800 rounded p-3 border border-slate-700">
          <div className="text-slate-400 text-xs">Cash</div>
          <div className="text-white font-mono text-lg">${stats?.cash.toFixed(2) ?? '--'}</div>
        </div>
        <div className="bg-slate-800 rounded p-3 border border-slate-700">
          <div className="text-slate-400 text-xs">Total PnL</div>
          <div className={`font-mono text-lg ${(stats?.total_pnl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            ${stats?.total_pnl.toFixed(2) ?? '--'}
          </div>
        </div>
        <div className="bg-slate-800 rounded p-3 border border-slate-700">
          <div className="text-slate-400 text-xs">Win Rate</div>
          <div className="text-white font-mono text-lg">{((stats?.win_rate ?? 0) * 100).toFixed(1)}%</div>
        </div>
        <div className="bg-slate-800 rounded p-3 border border-slate-700">
          <div className="text-slate-400 text-xs">Trades</div>
          <div className="text-white font-mono text-lg">{stats?.total_trades ?? '--'}</div>
        </div>
      </div>

      {/* Order Form */}
      <div className="bg-slate-800 rounded p-4 border border-slate-700">
        <h3 className="text-slate-200 font-semibold mb-3">Place Paper Order</h3>
        <div className="flex flex-wrap gap-3 items-end">
          <div>
            <label className="text-xs text-slate-400">Symbol</label>
            <input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              className="block w-28 bg-slate-900 border border-slate-600 rounded px-2 py-1 text-sm text-white"
            />
          </div>
          <div>
            <label className="text-xs text-slate-400">Side</label>
            <select
              value={side}
              onChange={(e) => setSide(e.target.value as any)}
              className="block w-24 bg-slate-900 border border-slate-600 rounded px-2 py-1 text-sm text-white"
            >
              <option value="buy">Buy</option>
              <option value="sell">Sell</option>
            </select>
          </div>
          <div>
            <label className="text-xs text-slate-400">Qty</label>
            <input
              value={qty}
              onChange={(e) => setQty(e.target.value)}
              className="block w-24 bg-slate-900 border border-slate-600 rounded px-2 py-1 text-sm text-white"
            />
          </div>
          <div>
            <label className="text-xs text-slate-400">Limit Price (0=market)</label>
            <input
              value={price}
              onChange={(e) => setPrice(e.target.value)}
              placeholder="0"
              className="block w-28 bg-slate-900 border border-slate-600 rounded px-2 py-1 text-sm text-white"
            />
          </div>
          <button
            onClick={placeOrder}
            disabled={loading}
            className={`px-4 py-1.5 rounded text-sm font-medium ${
              side === 'buy' ? 'bg-green-600 hover:bg-green-500' : 'bg-red-600 hover:bg-red-500'
            } text-white disabled:opacity-50`}
          >
            {loading ? '...' : side === 'buy' ? 'Buy' : 'Sell'}
          </button>
        </div>
      </div>

      {/* Positions */}
      <div className="bg-slate-800 rounded p-4 border border-slate-700">
        <h3 className="text-slate-200 font-semibold mb-3">Positions</h3>
        {positions.length === 0 ? (
          <div className="text-slate-500 text-sm">No positions</div>
        ) : (
          <table className="w-full text-sm text-left">
            <thead>
              <tr className="text-slate-400 border-b border-slate-700">
                <th className="py-1">Symbol</th>
                <th>Side</th>
                <th>Qty</th>
                <th>Avg Price</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p, i) => (
                <tr key={i} className="border-b border-slate-700/50 text-slate-200">
                  <td className="py-1">{p.symbol}</td>
                  <td className={p.side === 'long' ? 'text-green-400' : 'text-red-400'}>{p.side}</td>
                  <td>{p.quantity}</td>
                  <td>${p.avg_price.toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Orders */}
      <div className="bg-slate-800 rounded p-4 border border-slate-700">
        <h3 className="text-slate-200 font-semibold mb-3">Orders</h3>
        {orders.length === 0 ? (
          <div className="text-slate-500 text-sm">No orders</div>
        ) : (
          <table className="w-full text-sm text-left">
            <thead>
              <tr className="text-slate-400 border-b border-slate-700">
                <th className="py-1">ID</th>
                <th>Symbol</th>
                <th>Side</th>
                <th>Type</th>
                <th>Qty</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o, i) => (
                <tr key={i} className="border-b border-slate-700/50 text-slate-200">
                  <td className="py-1 font-mono text-xs">{o.id.slice(0, 12)}</td>
                  <td>{o.symbol}</td>
                  <td className={o.side === 'buy' ? 'text-green-400' : 'text-red-400'}>{o.side}</td>
                  <td className="capitalize">{o.order_type}</td>
                  <td>{o.filled_qty}/{o.quantity}</td>
                  <td className="capitalize">{o.status}</td>
                  <td>
                    {o.status === 'pending' && (
                      <button
                        onClick={() => cancelOrder(o.id)}
                        className="text-xs text-slate-400 hover:text-white underline"
                      >
                        Cancel
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
