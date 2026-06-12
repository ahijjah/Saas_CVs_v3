import React from 'react';
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, Cell } from 'recharts';
import { RecruiterEfficiencyMetric } from '../../types';

interface RecruiterEfficiencyChartProps {
  recruiters: RecruiterEfficiencyMetric[];
  emptyText: string;
}

const FASTEST_FILL = '#10b981';
const BAR_FILL = '#6366f1';

const EfficiencyTooltip: React.FC<any> = ({ active, payload, label }) => {
  if (!active || !payload || !payload.length) return null;
  const item = payload[0]?.payload as RecruiterEfficiencyMetric & { isFastest: boolean };
  if (!item) return null;
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip-title">
        {label} {item.isFastest && <span className="top-performer-badge">Fastest</span>}
      </div>
      <div>
        Avg time to hire: <strong>{item.avg_time_to_hire_days !== null ? `${item.avg_time_to_hire_days.toFixed(1)}d` : '—'}</strong>
      </div>
      <div>Hires completed: <strong>{item.hires_completed}</strong></div>
      <div>Candidates managed: <strong>{item.candidates_managed}</strong></div>
    </div>
  );
};

const RecruiterEfficiencyChart: React.FC<RecruiterEfficiencyChartProps> = ({ recruiters, emptyText }) => {
  const withHires = recruiters.filter((r) => r.avg_time_to_hire_days !== null);

  if (withHires.length === 0) {
    return <div className="chart-empty">{emptyText}</div>;
  }

  const fastest = Math.min(...withHires.map((r) => r.avg_time_to_hire_days as number));
  const data = withHires
    .map((r) => ({ ...r, name: r.recruiter_name, isFastest: r.avg_time_to_hire_days === fastest }))
    .sort((a, b) => (a.avg_time_to_hire_days as number) - (b.avg_time_to_hire_days as number));

  return (
    <ResponsiveContainer width="100%" height={Math.max(220, data.length * 52)}>
      <BarChart data={data} layout="vertical" margin={{ top: 8, right: 32, left: 8, bottom: 8 }}>
        <XAxis
          type="number"
          allowDecimals={false}
          tick={{ fontSize: 12, fill: '#6b7280' }}
          label={{ value: 'Avg days to hire', position: 'insideBottom', offset: -4, fontSize: 11, fill: '#9ca3af' }}
        />
        <YAxis type="category" dataKey="name" width={140} tick={{ fontSize: 13, fill: '#1f2937' }} />
        <Tooltip content={<EfficiencyTooltip />} cursor={{ fill: 'rgba(99, 102, 241, 0.06)' }} />
        <Bar dataKey="avg_time_to_hire_days" name="Avg days to hire" radius={[0, 4, 4, 0]} barSize={20}>
          {data.map((entry, index) => (
            <Cell key={`cell-${index}`} fill={entry.isFastest ? FASTEST_FILL : BAR_FILL} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
};

export { RecruiterEfficiencyChart };
