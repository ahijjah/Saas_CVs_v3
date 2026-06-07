import React from 'react';
import { ResponsiveContainer, PieChart, Pie, Cell, Tooltip, Legend } from 'recharts';

interface PipelineSnapshotChartProps {
  awaitingReview: number;
  underReview: number;
  interviewing: number;
  offerMade: number;
  hired: number;
  labels: {
    awaitingReview: string;
    underReview: string;
    interviewing: string;
    offerMade: string;
    hired: string;
  };
  emptyText: string;
}

// Mirrors the existing PipelineRow bar colors (sky / blue / purple / amber / green)
const STAGE_COLORS = ['#38bdf8', '#60a5fa', '#a78bfa', '#fbbf24', '#4ade80'];

const SnapshotTooltip: React.FC<any> = ({ active, payload }) => {
  if (!active || !payload || !payload.length) return null;
  const item = payload[0].payload;
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip-title">{item.name}</div>
      <div>Count: <strong>{item.value}</strong></div>
      <div>Share: <strong>{item.percentage}%</strong></div>
    </div>
  );
};

const PipelineSnapshotChart: React.FC<PipelineSnapshotChartProps> = ({
  awaitingReview, underReview, interviewing, offerMade, hired, labels, emptyText,
}) => {
  const raw = [
    { name: labels.awaitingReview, value: awaitingReview },
    { name: labels.underReview, value: underReview },
    { name: labels.interviewing, value: interviewing },
    { name: labels.offerMade, value: offerMade },
    { name: labels.hired, value: hired },
  ];
  const total = raw.reduce((sum, d) => sum + d.value, 0);

  if (total === 0) {
    return <div className="chart-empty">{emptyText}</div>;
  }

  const data = raw
    .filter((d) => d.value > 0)
    .map((d) => ({ ...d, percentage: ((d.value / total) * 100).toFixed(1) }));

  return (
    <ResponsiveContainer width="100%" height={220}>
      <PieChart>
        <Tooltip content={<SnapshotTooltip />} />
        <Legend verticalAlign="bottom" height={48} wrapperStyle={{ fontSize: 11 }} />
        <Pie data={data} dataKey="value" nameKey="name" innerRadius={52} outerRadius={80} paddingAngle={2}>
          {data.map((_, index) => (
            <Cell key={`cell-${index}`} fill={STAGE_COLORS[index % STAGE_COLORS.length]} />
          ))}
        </Pie>
      </PieChart>
    </ResponsiveContainer>
  );
};

export { PipelineSnapshotChart };
