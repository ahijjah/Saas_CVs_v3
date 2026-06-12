import React from 'react';
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, Cell } from 'recharts';

interface AttentionItem {
  key: string;
  label: string;
  value: number;
  color: string;
}

interface AttentionSummaryChartProps {
  items: AttentionItem[];
  emptyText: string;
}

const AttentionTooltip: React.FC<any> = ({ active, payload }) => {
  if (!active || !payload || !payload.length) return null;
  const item = payload[0].payload as AttentionItem;
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip-title">{item.label}</div>
      <div>Count: <strong>{item.value}</strong></div>
    </div>
  );
};

const AttentionSummaryChart: React.FC<AttentionSummaryChartProps> = ({ items, emptyText }) => {
  const data = items.filter((i) => i.value > 0);

  if (data.length === 0) {
    return <div className="chart-empty">{emptyText}</div>;
  }

  return (
    <ResponsiveContainer width="100%" height={Math.max(160, data.length * 42)}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 24, left: 4, bottom: 4 }}>
        <XAxis type="number" allowDecimals={false} tick={{ fontSize: 11, fill: '#94a3b8' }} />
        <YAxis type="category" dataKey="label" width={130} tick={{ fontSize: 12, fill: '#475569' }} />
        <Tooltip content={<AttentionTooltip />} cursor={{ fill: 'rgba(15, 23, 42, 0.04)' }} />
        <Bar dataKey="value" radius={[0, 4, 4, 0]} barSize={18}>
          {data.map((entry) => (
            <Cell key={entry.key} fill={entry.color} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
};

export { AttentionSummaryChart };
export type { AttentionItem };
