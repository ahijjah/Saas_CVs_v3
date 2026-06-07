import React from 'react';
import { ResponsiveContainer, PieChart, Pie, Cell, Tooltip, Legend } from 'recharts';

interface SLADistributionChartProps {
  redCount: number;
  amberCount: number;
  greenCount: number;
  activeFilter: string | null;
  onSliceClick: (status: 'red' | 'amber' | 'green') => void;
}

const SLA_COLORS: Record<string, string> = {
  red: '#ef4444',
  amber: '#f59e0b',
  green: '#10b981',
};

const SLA_NAMES: Record<string, string> = {
  red: 'Overdue',
  amber: 'Approaching SLA',
  green: 'On Track',
};

const SLATooltip: React.FC<any> = ({ active, payload }) => {
  if (!active || !payload || !payload.length) return null;
  const item = payload[0].payload;
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip-title">{item.name}</div>
      <div>Candidates: <strong>{item.value}</strong></div>
      <div>Share: <strong>{item.percentage}%</strong></div>
    </div>
  );
};

const SLADistributionChart: React.FC<SLADistributionChartProps> = ({
  redCount,
  amberCount,
  greenCount,
  activeFilter,
  onSliceClick,
}) => {
  const total = redCount + amberCount + greenCount;
  if (total === 0) {
    return <div className="chart-empty">No active applications to monitor for SLA.</div>;
  }

  const raw: { key: 'red' | 'amber' | 'green'; count: number }[] = [
    { key: 'red', count: redCount },
    { key: 'amber', count: amberCount },
    { key: 'green', count: greenCount },
  ];

  const data = raw
    .filter((d) => d.count > 0)
    .map((d) => ({
      key: d.key,
      name: SLA_NAMES[d.key],
      value: d.count,
      percentage: ((d.count / total) * 100).toFixed(1),
    }));

  return (
    <ResponsiveContainer width="100%" height={260}>
      <PieChart>
        <Tooltip content={<SLATooltip />} />
        <Legend verticalAlign="bottom" height={36} />
        <Pie
          data={data}
          dataKey="value"
          nameKey="name"
          innerRadius={64}
          outerRadius={96}
          paddingAngle={2}
          onClick={(entry: any) => onSliceClick(entry.key)}
          cursor="pointer"
        >
          {data.map((entry) => (
            <Cell
              key={entry.key}
              fill={SLA_COLORS[entry.key]}
              opacity={activeFilter && activeFilter !== entry.key ? 0.35 : 1}
              stroke={activeFilter === entry.key ? '#1f2937' : 'none'}
              strokeWidth={activeFilter === entry.key ? 2 : 0}
            />
          ))}
        </Pie>
      </PieChart>
    </ResponsiveContainer>
  );
};

export { SLADistributionChart };
