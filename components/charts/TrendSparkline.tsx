import React from 'react';
import { ResponsiveContainer, AreaChart, Area, XAxis, Tooltip } from 'recharts';

interface TrendPoint {
  date: string;
  value: number;
}

interface TrendSparklineProps {
  title: string;
  total: number;
  totalLabel: string;
  data: TrendPoint[];
  color: string;
  emptyText: string;
}

const formatDateLabel = (iso: string) => {
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
};

const TrendTooltip: React.FC<any> = ({ active, payload, label, color }) => {
  if (!active || !payload || !payload.length) return null;
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip-title">{formatDateLabel(label)}</div>
      <div>
        Count: <strong style={{ color }}>{payload[0].value}</strong>
      </div>
    </div>
  );
};

const TrendSparkline: React.FC<TrendSparklineProps> = ({ title, total, totalLabel, data, color, emptyText }) => {
  const gradientId = `trend-gradient-${title.replace(/\s+/g, '-').toLowerCase()}`;

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">{title}</span>
        <span className="text-lg font-bold" style={{ color }}>
          {total} <span className="text-xs font-normal text-slate-400 normal-case">{totalLabel}</span>
        </span>
      </div>
      {data.length === 0 || total === 0 ? (
        <div className="text-center text-xs text-slate-400 py-8">{emptyText}</div>
      ) : (
        <ResponsiveContainer width="100%" height={96}>
          <AreaChart data={data} margin={{ top: 6, right: 8, left: 8, bottom: 0 }}>
            <defs>
              <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={color} stopOpacity={0.35} />
                <stop offset="95%" stopColor={color} stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <XAxis
              dataKey="date"
              tickFormatter={formatDateLabel}
              tick={{ fontSize: 10, fill: '#94a3b8' }}
              interval="preserveStartEnd"
              axisLine={false}
              tickLine={false}
              minTickGap={32}
            />
            <Tooltip content={<TrendTooltip color={color} />} />
            <Area
              type="monotone"
              dataKey="value"
              stroke={color}
              strokeWidth={2}
              fill={`url(#${gradientId})`}
              isAnimationActive
            />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  );
};

export { TrendSparkline };
export type { TrendPoint };
