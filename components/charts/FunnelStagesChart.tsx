import React from 'react';
import {
  ResponsiveContainer,
  FunnelChart,
  Funnel,
  Cell,
  LabelList,
  Tooltip,
} from 'recharts';
import { FunnelStageMetric } from '../../types';

interface FunnelStagesChartProps {
  stages: FunnelStageMetric[];
}

const STAGE_COLORS = ['#3b82f6', '#6366f1', '#8b5cf6', '#a855f7', '#ec4899', '#f43f5e', '#ef4444'];

const FunnelTooltip: React.FC<any> = ({ active, payload }) => {
  if (!active || !payload || !payload.length) return null;
  const item = payload[0].payload as FunnelStageMetric & { name: string };
  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip-title">{item.name}</div>
      <div>Count: <strong>{item.stage_count}</strong></div>
      <div>Share of pipeline: <strong>{item.percentage_of_pipeline}%</strong></div>
      <div>
        Conversion from previous:{' '}
        <strong>{item.conversion_from_previous !== null ? `${item.conversion_from_previous}%` : '—'}</strong>
      </div>
      <div>
        Avg days in stage:{' '}
        <strong>{item.avg_days_in_stage !== null ? item.avg_days_in_stage.toFixed(1) : '—'}</strong>
      </div>
    </div>
  );
};

const FunnelStagesChart: React.FC<FunnelStagesChartProps> = ({ stages }) => {
  if (!stages || stages.length === 0) {
    return <div className="chart-empty">No funnel data for the selected range.</div>;
  }

  const data = stages.map((stage) => ({
    ...stage,
    name: stage.workflow_status.replace(/_/g, ' ').toUpperCase(),
    value: stage.stage_count,
  }));

  return (
    <ResponsiveContainer width="100%" height={Math.max(280, stages.length * 64)}>
      <FunnelChart>
        <Tooltip content={<FunnelTooltip />} />
        <Funnel dataKey="value" data={data} isAnimationActive nameKey="name">
          <LabelList
            dataKey="name"
            position="right"
            fill="#1f2937"
            stroke="none"
            fontSize={13}
            fontWeight={600}
            offset={12}
          />
          <LabelList
            dataKey="stage_count"
            position="center"
            fill="#ffffff"
            stroke="none"
            fontSize={14}
            fontWeight={700}
            formatter={(v: number) => `${v}`}
          />
          {data.map((_, index) => (
            <Cell key={`cell-${index}`} fill={STAGE_COLORS[index % STAGE_COLORS.length]} />
          ))}
        </Funnel>
      </FunnelChart>
    </ResponsiveContainer>
  );
};

export { FunnelStagesChart };
