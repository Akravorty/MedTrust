import { useState, useEffect } from 'react';
import { BatchDecision } from '../types/schema';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';
import { AlertCircle, CheckCircle2, ShieldAlert } from 'lucide-react';

interface Props {
  decision: BatchDecision;
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: Array<{ value: number; payload: { name: string; impact: number } }>;
  label?: string;
  accentColor: string;
}

function CustomChartTooltip({ active, payload, label, accentColor }: CustomTooltipProps) {
  if (active && payload && payload.length) {
    const value = payload[0].value;
    return (
      <div className="chart-glass-tooltip">
        <span className="chart-tooltip-label">{label || payload[0].payload.name}</span>
        <div className="chart-tooltip-value-row">
          <span className="chart-tooltip-metric">SHAP Impact:</span>
          <span className="chart-tooltip-val" style={{ color: accentColor }}>
            {value > 0 ? `+${value.toFixed(2)}` : value.toFixed(2)}
          </span>
        </div>
      </div>
    );
  }
  return null;
}

export default function DecisionScreen({ decision }: Props) {
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    const timer = setTimeout(() => setLoading(false), 450);
    return () => clearTimeout(timer);
  }, [decision.batch_id]);

  const isAccept = decision.status === 'ACCEPT';
  const isHold = decision.status === 'HOLD' || decision.status === 'MANUAL_REVIEW';
  const isReject = decision.status === 'REJECT';

  // Config for HOLD / ACCEPT / REJECT
  let color = '#92400e'; // text-amber-800
  let bg = '#fffbeb'; // bg-amber-50
  let border = '1px solid #fde68a'; // border-amber-200
  let iconBg = '#fef3c7'; // bg-amber-100
  let iconColor = '#d97706'; // text-amber-600
  let badgeBg = '#fef3c7'; // bg-amber-100
  let badgeColor = '#78350f'; // text-amber-900
  let badgeBorder = '1px solid #fde68a'; // border-amber-200
  let gradientId = 'shap-gradient-amber';
  let tooltipAccent = '#f59e0b';
  let glowColor = 'rgba(245, 158, 11, 0.5)';
  let activeFill = '#f97316';
  let Icon = AlertCircle;

  if (isAccept) {
    color = '#065f46'; // text-emerald-800
    bg = '#ecfdf5'; // bg-emerald-50
    border = '1px solid #a7f3d0';
    iconBg = '#d1fae5';
    iconColor = '#059669';
    badgeBg = '#d1fae5';
    badgeColor = '#064e3b';
    badgeBorder = '1px solid #a7f3d0';
    gradientId = 'shap-gradient-emerald';
    tooltipAccent = '#10b981';
    glowColor = 'rgba(16, 185, 129, 0.5)';
    activeFill = '#10b981';
    Icon = CheckCircle2;
  } else if (isReject) {
    color = '#991b1b'; // text-red-800
    bg = '#fef2f2'; // bg-red-50
    border = '1px solid #fca5a5';
    iconBg = '#fee2e2';
    iconColor = '#dc2626';
    badgeBg = '#fee2e2';
    badgeColor = '#7f1d1d';
    badgeBorder = '1px solid #fca5a5';
    gradientId = 'shap-gradient-red';
    tooltipAccent = '#ef4444';
    glowColor = 'rgba(239, 68, 68, 0.5)';
    activeFill = '#ef4444';
    Icon = ShieldAlert;
  }

  const chartData = decision.features.map(f => ({
    name: f.display_label,
    impact: f.value
  }));

  if (loading) {
    return (
      <div className="card shadow-panel animate-pulse" style={{ padding: '1.8rem' }}>
        <div style={{ height: '76px', background: '#f1f5f9', borderRadius: '14px', marginBottom: '1.5rem' }} />
        <div style={{ height: '24px', width: '40%', background: '#e2e8f0', borderRadius: '6px', marginBottom: '1rem' }} />
        <div style={{ height: '180px', background: '#f8fafc', borderRadius: '12px', border: '1px dashed #cbd5e1' }} />
      </div>
    );
  }

  return (
    <div className="card shadow-panel">
      {/* ── Status Header ── */}
      <div 
        className="status-header-pill"
        style={{
          backgroundColor: bg,
          border: border,
          borderRadius: '14px',
          padding: '1.1rem 1.4rem',
          marginBottom: '1.5rem',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '1rem',
          boxShadow: '0 2px 6px rgba(0, 0, 0, 0.02)'
        }}
      >
        <div className="flex-row items-center gap-4">
          <div 
            style={{
              backgroundColor: iconBg,
              color: iconColor,
              padding: '0.75rem',
              borderRadius: '12px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <Icon size={32} strokeWidth={2.2} />
          </div>
          <div>
            <span style={{ fontSize: '0.72rem', fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.08em', display: 'block' }}>
              Quality Gate Result
            </span>
            <h2 style={{ fontSize: '2.1rem', fontWeight: 800, color: color, margin: 0, lineHeight: 1.1 }}>
              {decision.status}
            </h2>
          </div>
        </div>

        {/* Confidence Badge */}
        <span 
          style={{
            backgroundColor: badgeBg,
            color: badgeColor,
            border: badgeBorder,
            padding: '0.4rem 0.9rem',
            borderRadius: '10px',
            fontSize: '0.88rem',
            fontWeight: 700,
            whiteSpace: 'nowrap',
            boxShadow: '0 1px 2px rgba(0,0,0,0.04)'
          }}
        >
          {(decision.confidence * 100).toFixed(1)}% Conf
        </span>
      </div>

      {/* ── SHAP Bar Chart ── */}
      <div>
        <div className="flex-row items-center justify-between" style={{ marginBottom: '0.85rem' }}>
          <h3 style={{ fontSize: '0.98rem', fontWeight: 700, color: '#0f172a' }} className="shap-title">
            Key Risk Factors (SHAP Impact)
          </h3>
          <span style={{ fontSize: '0.72rem', color: '#64748b', fontWeight: 500 }}>Higher = Increased Risk</span>
        </div>

        <div style={{ height: '210px', width: '100%' }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} layout="vertical" margin={{ top: 5, right: 30, left: 10, bottom: 5 }}>
              <defs>
                <linearGradient id="shap-gradient-amber" x1="0" y1="0" x2="1" y2="0">
                  <stop offset="0%" stopColor="#f59e0b" />
                  <stop offset="100%" stopColor="#f97316" />
                </linearGradient>
                <linearGradient id="shap-gradient-emerald" x1="0" y1="0" x2="1" y2="0">
                  <stop offset="0%" stopColor="#10b981" />
                  <stop offset="100%" stopColor="#14b8a6" />
                </linearGradient>
                <linearGradient id="shap-gradient-red" x1="0" y1="0" x2="1" y2="0">
                  <stop offset="0%" stopColor="#ef4444" />
                  <stop offset="100%" stopColor="#dc2626" />
                </linearGradient>
              </defs>

              {/* Light background gridlines */}
              <CartesianGrid stroke="#f1f5f9" strokeDasharray="3 3" horizontal={false} />
              
              <XAxis
                type="number"
                axisLine={{ stroke: '#cbd5e1', strokeWidth: 1.5 }}
                tickLine={{ stroke: '#cbd5e1' }}
                tick={{ fill: '#64748b', fontSize: 11, fontWeight: 500 }}
              />
              
              <YAxis
                dataKey="name"
                type="category"
                width={155}
                axisLine={{ stroke: '#cbd5e1', strokeWidth: 1.5 }}
                tickLine={{ stroke: '#cbd5e1' }}
                tick={{ fill: '#1e293b', fontSize: 13, fontWeight: 600 }}
                className="shap-yaxis-tick"
              />
              
              <Tooltip
                content={<CustomChartTooltip accentColor={tooltipAccent} />}
                wrapperStyle={{ outline: 'none', zIndex: 100 }}
                contentStyle={{
                  background: '#0f172a',
                  color: '#f8fafc',
                  border: '1px solid #334155',
                  borderRadius: '8px',
                  boxShadow: '0 10px 25px -5px rgba(0, 0, 0, 0.25)'
                }}
                cursor={{ fill: 'rgba(2, 132, 199, 0.04)' }}
              />
              
              <Bar
                dataKey="impact"
                fill={`url(#${gradientId})`}
                radius={[0, 6, 6, 0]}
                barSize={22}
                className="shap-bar"
                activeBar={{
                  fill: activeFill,
                  stroke: tooltipAccent,
                  strokeWidth: 1.5,
                  style: { filter: `drop-shadow(0 0 8px ${glowColor})` }
                }}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
