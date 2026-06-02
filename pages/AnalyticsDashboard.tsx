import React, { useState, useEffect } from 'react';
import {
  FunnelMetricsResponse,
  RecruiterProductivityResponse,
  AgingMetricsResponse,
  InsightsResponse,
  RecruitmentInsight,
} from '../types';
import { APIService } from '../services/api';
import { usePageTitle } from '../context/PageTitleContext';
import '../styles/analytics.css';

interface AnalyticsDashboardProps {
  auth: any;
  addToast: (message: string, type: 'success' | 'error' | 'warning' | 'info') => void;
}

const SLA_LABELS: Record<string, string> = {
  green: 'On Track',
  amber: 'Approaching SLA',
  red: 'Overdue',
};

const AnalyticsDashboard: React.FC<AnalyticsDashboardProps> = ({ auth, addToast }) => {
  const { setPageTitle } = usePageTitle();

  useEffect(() => {
    setPageTitle('Analytics');
    return () => { setPageTitle(null); };
  }, [setPageTitle]);

  const [dateFrom, setDateFrom] = useState<string>(
    new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10)
  );
  const [dateTo, setDateTo] = useState<string>(new Date().toISOString().slice(0, 10));
  const [loading, setLoading] = useState(true);

  const [funnelMetrics, setFunnelMetrics] = useState<FunnelMetricsResponse | null>(null);
  const [recruiterMetrics, setRecruiterMetrics] = useState<RecruiterProductivityResponse | null>(null);
  const [agingMetrics, setAgingMetrics] = useState<AgingMetricsResponse | null>(null);
  const [agingCurrentPage, setAgingCurrentPage] = useState(1);
  const [slaFilter, setSlaFilter] = useState<string | null>(null);
  const [insights, setInsights] = useState<InsightsResponse | null>(null);
  const pageSize = 10;

  const api = new APIService(auth.token);

  const loadAnalytics = async () => {
    try {
      setLoading(true);
      setAgingCurrentPage(1);

      const [funnel, productivity, aging, insightsData] = await Promise.all([
        api.getFunnelMetrics({ date_from: dateFrom, date_to: dateTo }),
        api.getRecruiterProductivity({ date_from: dateFrom, date_to: dateTo }),
        api.getAgingMetrics({ days_threshold: 0 }),
        api.getInsights(),
      ]);

      setFunnelMetrics(funnel);
      setRecruiterMetrics(productivity);
      setAgingMetrics(aging);
      setInsights(insightsData);
    } catch (error: any) {
      addToast(error.message || 'Failed to load analytics', 'error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAnalytics();
  }, [dateFrom, dateTo]);

  useEffect(() => {
    setAgingCurrentPage(1);
  }, [slaFilter]);

  if (loading) {
    return <div className="analytics-loading">Loading analytics...</div>;
  }

  const totalApps = funnelMetrics?.total_applications || 0;
  const hiredCount = funnelMetrics?.stages.find((s) => s.workflow_status === 'hired')?.stage_count || 0;
  const hireRate = totalApps > 0 ? ((hiredCount / totalApps) * 100).toFixed(1) : 0;

  return (
    <div className="analytics-dashboard">
      <div className="analytics-header">
        <h1>Recruitment Analytics</h1>
        <div className="analytics-filters">
          <div className="filter-group">
            <label>From</label>
            <input
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
            />
          </div>
          <div className="filter-group">
            <label>To</label>
            <input
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
            />
          </div>
          <button onClick={loadAnalytics} className="btn-refresh">
            Refresh
          </button>
        </div>
      </div>

      {/* KPI Cards */}
      <div className="kpi-cards">
        <div className="kpi-card">
          <div className="kpi-value">{totalApps}</div>
          <div className="kpi-label">Total Applications</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{hiredCount}</div>
          <div className="kpi-label">Hired</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{hireRate}%</div>
          <div className="kpi-label">Hire Rate</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-value">{agingMetrics?.red_count || 0}</div>
          <div className="kpi-label">
            <span style={{ color: '#ef4444' }}>Overdue</span>
          </div>
        </div>
      </div>

      {/* Funnel Stages */}
      <section className="analytics-section">
        <h2>Recruitment Funnel</h2>
        <div className="funnel-table">
          <table>
            <thead>
              <tr>
                <th>Stage</th>
                <th>Count</th>
                <th>% of Pipeline</th>
                <th>From Previous</th>
                <th>Avg Days</th>
              </tr>
            </thead>
            <tbody>
              {funnelMetrics?.stages.map((stage) => (
                <tr key={stage.workflow_status}>
                  <td className="stage-name">
                    {stage.workflow_status.replace(/_/g, ' ').toUpperCase()}
                  </td>
                  <td>{stage.stage_count}</td>
                  <td>{stage.percentage_of_pipeline}%</td>
                  <td>
                    {stage.conversion_from_previous !== null
                      ? `${stage.conversion_from_previous}%`
                      : '—'}
                  </td>
                  <td>
                    {stage.avg_days_in_stage !== null
                      ? stage.avg_days_in_stage.toFixed(1)
                      : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Recruiter Productivity */}
      <section className="analytics-section">
        <h2>Recruiter Productivity</h2>
        <div className="productivity-table">
          <table>
            <thead>
              <tr>
                <th>Recruiter</th>
                <th>Apps</th>
                <th>In Review</th>
                <th>Moves</th>
                <th>Interviews</th>
                <th>Feedback</th>
                <th>Approvals</th>
              </tr>
            </thead>
            <tbody>
              {recruiterMetrics?.recruiters.map((rec) => (
                <tr key={rec.user_id}>
                  <td>{rec.recruiter_name}</td>
                  <td>{rec.total_applications_assigned}</td>
                  <td>{rec.applications_in_review}</td>
                  <td>{rec.workflow_moves_made}</td>
                  <td>{rec.interviews_completed}</td>
                  <td>{rec.feedback_provided}</td>
                  <td>{rec.approvals_decided}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* SLA Aging */}
      <section className="analytics-section">
        <h2>SLA Monitoring</h2>
        <div className="sla-alert-zones">
          <div
            className={`alert-zone red ${slaFilter === 'red' ? 'active' : ''}`}
            onClick={() => setSlaFilter(slaFilter === 'red' ? null : 'red')}
            style={{ cursor: 'pointer' }}
          >
            <div className="zone-count">{agingMetrics?.red_count || 0}</div>
            <div className="zone-label">Overdue</div>
          </div>
          <div
            className={`alert-zone amber ${slaFilter === 'amber' ? 'active' : ''}`}
            onClick={() => setSlaFilter(slaFilter === 'amber' ? null : 'amber')}
            style={{ cursor: 'pointer' }}
          >
            <div className="zone-count">{agingMetrics?.amber_count || 0}</div>
            <div className="zone-label">Approaching</div>
          </div>
          <div
            className={`alert-zone green ${slaFilter === 'green' ? 'active' : ''}`}
            onClick={() => setSlaFilter(slaFilter === 'green' ? null : 'green')}
            style={{ cursor: 'pointer' }}
          >
            <div className="zone-count">{agingMetrics?.green_count || 0}</div>
            <div className="zone-label">On Track</div>
          </div>
        </div>

        {slaFilter && (
          <div className="sla-filter-status">
            <span>
              Showing{' '}
              {slaFilter === 'red'
                ? 'Overdue'
                : slaFilter === 'amber'
                ? 'Approaching SLA'
                : 'On Track'}{' '}
              candidates
            </span>
            <button
              onClick={() => setSlaFilter(null)}
              className="clear-filter-btn"
            >
              Clear filter
            </button>
          </div>
        )}

        <div className="aging-table">
          <table>
            <thead>
              <tr>
                <th>Candidate</th>
                <th>Job</th>
                <th>Status</th>
                <th>Assigned To</th>
                <th>Days In Status</th>
                <th>SLA</th>
              </tr>
            </thead>
            <tbody>
              {agingMetrics && agingMetrics.metrics.length > 0 ? (
                (() => {
                  const filteredMetrics = slaFilter
                    ? agingMetrics.metrics.filter(m => m.sla_status === slaFilter)
                    : agingMetrics.metrics;
                  const startIdx = (agingCurrentPage - 1) * pageSize;
                  const endIdx = startIdx + pageSize;
                  const paginatedMetrics = filteredMetrics.slice(startIdx, endIdx);
                  return paginatedMetrics.map((metric) => (
                    <tr key={metric.application_id}>
                      <td>{metric.candidate_name}</td>
                      <td>{metric.job_title}</td>
                      <td>{metric.workflow_status.replace(/_/g, ' ')}</td>
                      <td>{metric.assigned_user_name || '—'}</td>
                      <td>{metric.days_in_status.toFixed(1)}d</td>
                      <td>
                        <span className={`sla-badge ${metric.sla_status}`}>
                          {SLA_LABELS[metric.sla_status] ?? metric.sla_status.toUpperCase()}
                        </span>
                      </td>
                    </tr>
                  ));
                })()
              ) : (
                <tr>
                  <td colSpan={6} style={{ textAlign: 'center', padding: '20px', color: '#999' }}>
                    No candidates {slaFilter ? 'with this SLA status' : 'on track'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {agingMetrics && agingMetrics.metrics.length > 0 && (
          <div className="pagination-controls">
            <div className="pagination-info">
              {(() => {
                const filteredMetrics = slaFilter
                  ? agingMetrics.metrics.filter(m => m.sla_status === slaFilter)
                  : agingMetrics.metrics;
                const totalFiltered = filteredMetrics.length;
                return totalFiltered > pageSize ? (
                  <>
                    <button
                      onClick={() => setAgingCurrentPage(p => Math.max(1, p - 1))}
                      disabled={agingCurrentPage === 1}
                      className="pagination-btn"
                    >
                      ← Previous
                    </button>
                    <span className="pagination-text">
                      Page {agingCurrentPage} of {Math.ceil(totalFiltered / pageSize)}
                    </span>
                    <button
                      onClick={() => setAgingCurrentPage(p => Math.min(Math.ceil(totalFiltered / pageSize), p + 1))}
                      disabled={agingCurrentPage === Math.ceil(totalFiltered / pageSize)}
                      className="pagination-btn"
                    >
                      Next →
                    </button>
                  </>
                ) : (
                  <span className="pagination-text">
                    Showing all {totalFiltered} candidate{totalFiltered !== 1 ? 's' : ''}
                  </span>
                );
              })()}
            </div>
          </div>
        )}
      </section>

      {/* ── Operational Insights ─────────────────────────────────────────── */}
      <section className="analytics-section">
        <div className="insights-header">
          <h2>Operational Insights</h2>
          <span className="insights-label">Rule-based insight</span>
        </div>

        {!insights || insights.insights.length === 0 ? (
          <div className="insights-empty">
            <svg className="insights-empty-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5"
                d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <p>No significant recruitment insights detected.</p>
          </div>
        ) : (
          <div className="insights-list">
            {insights.insights.map((insight: RecruitmentInsight) => (
              <div key={insight.insight_id} className={`insight-card insight-${insight.severity}`}>
                <div className="insight-card-header">
                  <span className={`insight-severity-badge severity-${insight.severity}`}>
                    {insight.severity.toUpperCase()}
                  </span>
                  <span className="insight-title">{insight.title}</span>
                </div>
                <p className="insight-description">{insight.description}</p>
                <p className="insight-action">
                  <strong>Suggested action:</strong> {insight.suggested_action}
                </p>
                {insight.metric_value !== null && insight.threshold !== null && (
                  <div className="insight-metric">
                    <span>Value: <strong>{insight.metric_value}</strong></span>
                    <span>Threshold: <strong>{insight.threshold}</strong></span>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
};

export { AnalyticsDashboard };
