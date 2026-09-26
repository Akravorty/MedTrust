import { useEffect, useState } from 'react';
import { History, ShieldCheck, UserPlus, Stethoscope, Route, FlaskConical, Ticket, Video, CalendarClock } from 'lucide-react';
import { useI18n } from '../../i18n';
import { getPatientTimeline, verifyPatientRecord } from '../../services/api';
import type { Patient, PatientTimelineEvent, PatientVerification } from '../../types/schema';

interface Props {
  patient: Patient;
}

const ACTION_META: Record<string, { icon: typeof UserPlus; color: string; bg: string }> = {
  PATIENT_REGISTERED:   { icon: UserPlus,       color: '#0284c7', bg: '#e0f2fe' },
  TRIAGE_COMPLETED:     { icon: Stethoscope,     color: '#7c3aed', bg: '#ede9fe' },
  REFERRAL_CREATED:     { icon: Route,           color: '#b45309', bg: '#fef3c7' },
  REFERRAL_ACCEPTED:    { icon: Route,           color: '#059669', bg: '#d1fae5' },
  REFERRAL_IN_TRANSIT:  { icon: Route,           color: '#b45309', bg: '#fef3c7' },
  REFERRAL_COMPLETED:   { icon: Route,           color: '#059669', bg: '#d1fae5' },
  DIAGNOSTIC_ORDERED:   { icon: FlaskConical,    color: '#7c3aed', bg: '#ede9fe' },
  DIAGNOSTIC_REVIEWED:  { icon: FlaskConical,    color: '#059669', bg: '#d1fae5' },
  QUEUE_JOINED:         { icon: Ticket,          color: '#059669', bg: '#d1fae5' },
  TELECONSULT_STARTED:  { icon: Video,           color: '#0284c7', bg: '#e0f2fe' },
  TELECONSULT_COMPLETE: { icon: Video,           color: '#059669', bg: '#d1fae5' },
  FOLLOW_UP_CREATED:    { icon: CalendarClock,   color: '#b91c1c', bg: '#fee2e2' },
  FOLLOW_UP_COMPLETED:  { icon: CalendarClock,   color: '#059669', bg: '#d1fae5' },
};

function fallbackMeta() {
  return { icon: History, color: '#6b7280', bg: '#f3f4f6' };
}

function relativeTime(timestamp: string): string {
  const diff = Date.now() - new Date(timestamp).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'Just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return new Date(timestamp).toLocaleDateString();
}

export default function PatientTimeline({ patient }: Props) {
  const { t } = useI18n();
  const [events, setEvents] = useState<PatientTimelineEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [verification, setVerification] = useState<PatientVerification | null>(null);

  useEffect(() => {
    setLoading(true);
    getPatientTimeline(patient.patient_id)
      .then((d) => setEvents(d.events))
      .catch(() => {})
      .finally(() => setLoading(false));
    verifyPatientRecord(patient.patient_id).then(setVerification).catch(() => {});
  }, [patient.patient_id]);

  if (loading) return null; // Don't block the triage screen

  return (
    <div className="card" style={{ marginTop: '1.25rem' }}>
      <div className="flex-row items-center justify-between" style={{ marginBottom: '1rem' }}>
        <div className="flex-row items-center gap-2">
          <History size={18} strokeWidth={2.2} color="var(--color-text-secondary)" />
          <h3 style={{ fontSize: '0.98rem', fontWeight: 700, color: 'var(--color-text-primary)' }}>
            {t('caTimelineTitle')}
          </h3>
          <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)', fontWeight: 500 }}>
            ({events.length} events)
          </span>
        </div>
        {verification?.valid && (
          <span className="ca-badge ca-badge--routine">
            <ShieldCheck size={13} /> {t('caRecordVerified')}
          </span>
        )}
      </div>

      {events.length === 0 ? (
        <div className="ca-empty-state" style={{ padding: '2rem', textAlign: 'center' }}>
          <History size={32} strokeWidth={1.5} style={{ opacity: 0.3, marginBottom: '0.75rem' }} />
          <div style={{ fontWeight: 600, marginBottom: '0.25rem' }}>No activity yet</div>
          <div style={{ fontSize: '0.82rem', color: 'var(--color-text-secondary)' }}>
            Events will appear here as the patient progresses through the care workflow.
          </div>
        </div>
      ) : (
        <div className="ca-timeline">
          {events.map((e, idx) => {
            const meta = ACTION_META[e.action] ?? fallbackMeta();
            const Icon = meta.icon;
            const isLast = idx === events.length - 1;
            return (
              <div key={e.event_id} className="ca-timeline-item" style={{ display: 'flex', gap: '0.75rem', paddingBottom: isLast ? 0 : '1rem', position: 'relative' }}>
                {/* Connector line */}
                {!isLast && (
                  <div style={{
                    position: 'absolute', left: '15px', top: '30px', bottom: 0, width: '2px',
                    background: 'var(--kpi-card-border)',
                  }} />
                )}
                {/* Icon */}
                <div style={{
                  flexShrink: 0, width: 32, height: 32, borderRadius: '50%',
                  background: meta.bg, color: meta.color,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  zIndex: 1,
                }}>
                  <Icon size={14} strokeWidth={2.5} />
                </div>
                {/* Content */}
                <div style={{ flex: 1, paddingTop: '0.35rem' }}>
                  <div className="ca-timeline-action" style={{ fontSize: '0.88rem', fontWeight: 600, color: 'var(--color-text-primary)' }}>
                    {e.action.replace(/_/g, ' ')}
                  </div>
                  <div className="ca-timeline-meta" style={{ fontSize: '0.78rem', color: 'var(--color-text-secondary)', marginTop: '0.15rem', display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                    <span>{e.actor}</span>
                    <span>·</span>
                    <span title={new Date(e.timestamp).toLocaleString()}>{relativeTime(e.timestamp)}</span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
