import { useEffect, useState } from 'react';
import { LayoutDashboard, Users, Stethoscope, AlertTriangle, CheckCircle2, Inbox, Ticket, CalendarClock, Pill, FlaskConical, RotateCw } from 'lucide-react';
import { useI18n } from '../../i18n';
import type { Key } from '../../i18n';
import { useFacilities, facilityLabel } from '../../hooks/useFacilities';
import { getDashboard } from '../../services/api';
import type { FacilityDashboardData } from '../../types/schema';

interface Props {
  defaultFacilityId?: string;
}

interface KpiSpec {
  key: keyof FacilityDashboardData | 'medicine';
  labelKey: Key;
  icon: typeof Users;
  accent: string;
  accentBg: string;
  value: (d: FacilityDashboardData) => string;
}

const KPIS: KpiSpec[] = [
  { key: 'patients_registered_today', labelKey: 'caPatientsToday', icon: Users, accent: '#0ea5e9', accentBg: '#e0f2fe', value: (d) => String(d.patients_registered_today) },
  { key: 'patients_triaged_today', labelKey: 'caTriagedToday', icon: Stethoscope, accent: '#7c3aed', accentBg: '#ede9fe', value: (d) => String(d.patients_triaged_today) },
  { key: 'emergency_flags_today', labelKey: 'caEmergenciesToday', icon: AlertTriangle, accent: '#dc2626', accentBg: '#fee2e2', value: (d) => String(d.emergency_flags_today) },
  { key: 'referrals_completed_total', labelKey: 'caReferralsCompleted', icon: CheckCircle2, accent: '#10b981', accentBg: '#d1fae5', value: (d) => String(d.referrals_completed_total) },
  { key: 'referrals_pending_incoming', labelKey: 'caReferralsPending', icon: Inbox, accent: '#f59e0b', accentBg: '#fef3c7', value: (d) => String(d.referrals_pending_incoming) },
  { key: 'queue_depth_now', labelKey: 'caQueueDepth', icon: Ticket, accent: '#0284c7', accentBg: '#e0f2fe', value: (d) => String(d.queue_depth_now) },
  { key: 'high_risk_follow_ups_overdue', labelKey: 'caOverdueFollowUps', icon: CalendarClock, accent: '#b91c1c', accentBg: '#fee2e2', value: (d) => String(d.high_risk_follow_ups_overdue) },
];

export default function FacilityDashboard({ defaultFacilityId }: Props) {
  const { t } = useI18n();
  const { facilities } = useFacilities();
  const [facilityId, setFacilityId] = useState(defaultFacilityId ?? '');
  const [data, setData] = useState<FacilityDashboardData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!facilityId && facilities.length) setFacilityId(defaultFacilityId ?? facilities[0].facility_id);
  }, [facilities, facilityId, defaultFacilityId]);

  const refresh = () => {
    if (!facilityId) return;
    setLoading(true);
    setError(null);
    getDashboard(facilityId)
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load dashboard'))
      .finally(() => setLoading(false));
  };

  useEffect(refresh, [facilityId]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="ca-wrap ca-wrap--wide animate-fade-in">
      <div className="flex-row items-center justify-between" style={{ marginBottom: '1.25rem', flexWrap: 'wrap', gap: '0.75rem' }}>
        <div className="flex-row items-center gap-3">
          <div style={{ background: '#e0f2fe', color: '#0284c7', padding: '0.6rem', borderRadius: '12px', display: 'flex' }}>
            <LayoutDashboard size={22} strokeWidth={2.2} />
          </div>
          <h2 className="ca-section-title" style={{ marginBottom: 0 }}>{t('caDashboardTitle')}</h2>
        </div>
        <div className="flex-row items-center gap-2">
          <select className="ca-select" value={facilityId} onChange={(e) => setFacilityId(e.target.value)} style={{ minWidth: '220px' }}>
            {facilities.map((f) => <option key={f.facility_id} value={f.facility_id}>{facilityLabel(f)}</option>)}
          </select>
          <button className="ca-btn ca-btn--sm ca-btn--secondary" onClick={refresh} disabled={loading}>
            <RotateCw size={14} />
          </button>
        </div>
      </div>

      {error && (
        <div role="alert" style={{ marginBottom: '1rem', padding: '0.75rem 1rem', borderRadius: '8px', fontWeight: 600, background: 'var(--color-reject-bg)', color: 'var(--color-reject)' }}>
          {error}
        </div>
      )}

      {data && (
        <div className="ca-kpi-grid">
          {KPIS.map(({ key, labelKey, icon: Icon, accent, accentBg, value }) => (
            <div key={key} className="ca-kpi-card">
              <div style={{ background: accentBg, color: accent, width: 34, height: 34, borderRadius: 9, display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: '0.6rem' }}>
                <Icon size={17} strokeWidth={2.2} />
              </div>
              <span className="ca-kpi-value" style={{ color: accent }}>{value(data)}</span>
              <span className="ca-kpi-label">{t(labelKey)}</span>
            </div>
          ))}

          <div className="ca-kpi-card">
            <div style={{ background: '#fef3c7', color: '#b45309', width: 34, height: 34, borderRadius: 9, display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: '0.6rem' }}>
              <Pill size={17} strokeWidth={2.2} />
            </div>
            <span className="ca-kpi-value" style={{ color: '#b45309' }}>
              {data.medicine_availability.pass_rate_pct !== null ? `${data.medicine_availability.pass_rate_pct}%` : '—'}
            </span>
            <span className="ca-kpi-label">
              {t('caMedicineAvailability')} · {data.medicine_availability.total_batches_tracked} tracked, {data.medicine_availability.flagged} flagged
            </span>
          </div>

          <div className="ca-kpi-card">
            <div style={{
              background: data.diagnostics.critical_awaiting_review > 0 ? '#fee2e2' : '#ede9fe',
              color: data.diagnostics.critical_awaiting_review > 0 ? '#b91c1c' : '#7c3aed',
              width: 34, height: 34, borderRadius: 9, display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: '0.6rem',
            }}>
              <FlaskConical size={17} strokeWidth={2.2} />
            </div>
            <span className="ca-kpi-value" style={{ color: data.diagnostics.critical_awaiting_review > 0 ? '#b91c1c' : '#7c3aed' }}>
              {data.diagnostics.pending}
            </span>
            <span className="ca-kpi-label">
              {t('caDiagnosticsPending')} · {data.diagnostics.awaiting_review} {t('caDiagnosticsAwaitingReview').toLowerCase()}
              {data.diagnostics.critical_awaiting_review > 0 && (
                <> · <strong style={{ color: '#b91c1c' }}>{data.diagnostics.critical_awaiting_review} {t('caDiagnosticsCritical').toLowerCase()}</strong></>
              )}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
