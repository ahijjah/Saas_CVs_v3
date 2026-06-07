import React from 'react';
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  Cell,
} from 'recharts';
import { RecruiterProductivityMetric } from '../../types';

interface RecruiterProductivityChartProps {
  recruiters: RecruiterProductivityMetric[];
}

const TOP_PERFORMER_FILL = '#10b981';
const BAR_FILL = '#3b82f6';

const ProductivityTooltip: React.FC<any> = ({ active, payload, label }) => {
  if (!active || !payload || !payload.length) return null;
  const item = payload[0]?.payload as RecruiterProductivityMetric & { isTopPerformer: boolean };
  if (!item) return null;
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip-title">
        {label} {item.isTopPerformer && <span className="top-performer-badge">Top performer</span>}
      </div>
      <div>Assigned: <strong>{item.total_applications_assigned}</strong></div>
      <div>In review: <strong>{item.applications_in_review}</strong></div>
      <div>Workflow moves: <strong>{item.workflow_moves_made}</strong></div>
      <div>Interviews completed: <strong>{item.interviews_completed}</strong></div>
      <div>Feedback provided: <strong>{item.feedback_provided}</strong></div>
      <div>Approvals decided: <strong>{item.approvals_decided}</strong></div>
    </div>
  );
};

const RecruiterProductivityChart: React.FC<RecruiterProductivityChartProps> = ({ recruiters }) => {
  if (!recruiters || recruiters.length === 0) {
    return <div className="chart-empty">No recruiter productivity data for the selected range.</div>;
  }

  const topAssigned = Math.max(...recruiters.map((r) => r.total_applications_assigned));
  const data = recruiters.map((r) => ({
    ...r,
    name: r.recruiter_name,
    isTopPerformer: r.total_applications_assigned === topAssigned && topAssigned > 0,
  }));

  return (
    <ResponsiveContainer width="100%" height={Math.max(280, recruiters.length * 56)}>
      <BarChart data={data} layout="vertical" margin={{ top: 8, right: 32, left: 8, bottom: 8 }}>
        <XAxis type="number" allowDecimals={false} tick={{ fontSize: 12, fill: '#6b7280' }} />
        <YAxis
          type="category"
          dataKey="name"
          width={140}
          tick={{ fontSize: 13, fill: '#1f2937' }}
        />
        <Tooltip content={<ProductivityTooltip />} cursor={{ fill: 'rgba(59, 130, 246, 0.06)' }} />
        <Legend
          payload={[
            { value: 'Applications assigned', type: 'square', color: BAR_FILL },
            { value: 'Top performer', type: 'square', color: TOP_PERFORMER_FILL },
          ]}
        />
        <Bar dataKey="total_applications_assigned" name="Applications assigned" radius={[0, 4, 4, 0]} barSize={22}>
          {data.map((entry, index) => (
            <Cell key={`cell-${index}`} fill={entry.isTopPerformer ? TOP_PERFORMER_FILL : BAR_FILL} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
};

export { RecruiterProductivityChart };
