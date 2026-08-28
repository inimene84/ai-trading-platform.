import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

export interface EquityPoint {
  time: string | number;
  value: number;
}

/**
 * Backtest equity curve.
 *
 * Split into its own module so `recharts` (~250 kB raw) is fetched only when a
 * backtest result is actually rendered, instead of shipping in the entry chunk.
 */
export default function EquityCurveChart({ data }: { data: EquityPoint[] }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" vertical={false} />
        <XAxis dataKey="time" hide />
        <YAxis
          domain={['auto', 'auto']}
          stroke="#4b5563"
          fontSize={10}
          tickLine={false}
          axisLine={false}
          tickFormatter={(v) => `$${v}`}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: '#111827',
            border: '1px solid #374151',
            borderRadius: '8px',
            fontSize: '10px',
          }}
          itemStyle={{ color: '#10b981' }}
        />
        <Line type="monotone" dataKey="value" stroke="#10b981" strokeWidth={2} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
