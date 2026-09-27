import { useCallback, useEffect, useState } from 'react';
import { Ticket, Clock, PhoneCall, PlayCircle, CheckCircle2, UserX, RotateCw, ArrowRight } from 'lucide-react';
import { useI18n } from '../../i18n';
import { joinQueue, getQueue, updateQueueTicketStatus } from '../../services/api';
import type { Patient, TriageResult, QueueTicket, QueueStatus } from '../../types/schema';

interface Props {
  patient: Patient;
  triage: TriageResult | null;
  defaultActor: string;
  onProceedToTeleconsult: () => void;
}

const NEXT_STATUS: Record<QueueStatus, { status: QueueStatus; icon: typeof PhoneCall }[]> = {
  WAITING: [{ status: 'CALLED', icon: PhoneCall }, { status: 'NO_SHOW', icon: UserX }],
  CALLED: [{ status: 'IN_CONSULT', icon: PlayCircle }, { status: 'NO_SHOW', icon: UserX }],
  IN_CONSULT: [{ status: 'DONE', icon: CheckCircle2 }],
  DONE: [],
  NO_SHOW: [],
};

export default function QueueScreen({ patient, triage, defaultActor, onProceedToTeleconsult }: Props) {
  const { t } = useI18n();
  const [ticket, setTicket] = useState<QueueTicket | null>(null);
  const [queue, setQueue] = useState<QueueTicket[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshQueue = useCallback((facilityId: string) => {
    getQueue(facilityId).then(setQueue).catch(() => {});
  }, []);

  useEffect(() => {
    if (ticket) refreshQueue(ticket.facility_id);
  }, [ticket, refreshQueue]);

  const handleJoin = async () => {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const priority = triage?.urgency === 'EMERGENCY';
      const t2 = await joinQueue(patient.home_facility_id, patient.patient_id, priority);
      setTicket(t2);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not join queue');
    } finally {
      setSubmitting(false);
    }
  };

  const advance = async (ticketId: string, status: QueueStatus) => {
    try {
      const updated = await updateQueueTicketStatus(ticketId, status, defaultActor);
      if (ticket?.ticket_id === ticketId) setTicket(updated);
      refreshQueue(updated.facility_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update ticket');
    }
  };

  return (
    <div className="ca-wrap ca-wrap--wide animate-fade-in">
      <div className="card">
        <div className="flex-row items-center gap-3" style={{ marginBottom: '0.4rem' }}>
          <div style={{ background: '#d1fae5', color: '#059669', padding: '0.6rem', borderRadius: '12px', display: 'flex' }}>
            <Ticket size={22} strokeWidth={2.2} />
          </div>
          <div>
            <h2 className="ca-section-title" style={{ marginBottom: 0 }}>{t('caQueueTitle')}</h2>
            <div className="ca-section-subtitle" style={{ marginBottom: 0 }}>
              {patient.name}, {patient.age} — {patient.home_facility_id}
            </div>
          </div>
        </div>

        {!ticket ? (
          <div style={{ marginTop: '1rem' }}>
            <p style={{ color: 'var(--color-text-secondary)', fontSize: '0.9rem', marginBottom: '1rem', lineHeight: 1.6 }}>
              The patient will be added to the facility queue. A token number will be assigned and an estimated wait time shown.
              {triage?.urgency === 'EMERGENCY' && (
                <strong style={{ display: 'block', marginTop: '0.4rem', color: '#dc2626' }}>
                  Emergency urgency — patient will be marked as priority.
                </strong>
              )}
            </p>
            <div className="ca-actions-row">
              <button className="ca-btn ca-btn--primary" onClick={handleJoin} disabled={submitting}>
                <Ticket size={16} />
                {submitting ? 'Joining…' : t('caJoinQueue')}
              </button>
            </div>
          </div>
        ) : (
          <div style={{ marginTop: '1.25rem' }}>
            <div className="flex-row items-center gap-4" style={{ flexWrap: 'wrap', alignItems: 'flex-start' }}>
              <div style={{
                textAlign: 'center',
                background: 'var(--bg-secondary)',
                border: '3px solid #0284c7',
                borderRadius: '16px',
                padding: '1rem 1.5rem',
                minWidth: '100px',
              }}>
                <div style={{ fontSize: '3rem', fontWeight: 900, color: '#0284c7', lineHeight: 1, letterSpacing: '-2px' }}>
                  #{ticket.token_number}
                </div>
                <div style={{ fontSize: '0.72rem', color: 'var(--color-text-secondary)', fontWeight: 700, marginTop: '0.3rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                  Token
                </div>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                <span className={`ca-badge ${ticket.priority ? 'ca-badge--emergency' : 'ca-badge--info'}`} style={{ width: 'fit-content' }}>
                  {ticket.priority && '⚡ '}{ticket.status.replace('_', ' ')}
                </span>
                {ticket.est_wait_minutes !== null && (
                  <span className="ca-badge ca-badge--neutral" style={{ width: 'fit-content' }}>
                    <Clock size={13} style={{ marginRight: 4 }} />~{ticket.est_wait_minutes} min wait
                  </span>
                )}
              </div>
            </div>
            {ticket.status === 'IN_CONSULT' && (
              <div className="ca-actions-row" style={{ marginTop: '1.25rem' }}>
                <button className="ca-btn ca-btn--success" onClick={onProceedToTeleconsult}>
                  Proceed to Teleconsultation <ArrowRight size={14} />
                </button>
              </div>
            )}
          </div>
        )}

        {error && (
          <div className="ca-alert ca-alert--error" role="alert" style={{ marginTop: '1rem' }}>
            {error}
          </div>
        )}
      </div>

      {queue.length > 0 && (
        <div className="card" style={{ marginTop: '1.25rem' }}>
          <div className="flex-row items-center justify-between" style={{ marginBottom: '0.85rem' }}>
            <h3 style={{ fontSize: '0.98rem', fontWeight: 700 }}>{t('caFrontDesk')}</h3>
            <button className="ca-btn ca-btn--sm ca-btn--secondary" onClick={() => ticket && refreshQueue(ticket.facility_id)}>
              <RotateCw size={14} />
            </button>
          </div>
          <div className="ca-list">
            {queue.map((q) => (
              <div key={q.ticket_id} className="ca-row">
                <div className="ca-row-main">
                  <span className="ca-row-title">
                    #{q.token_number} {q.priority && <span className="ca-badge ca-badge--emergency" style={{ marginLeft: 6 }}>PRIORITY</span>}
                  </span>
                  <span className="ca-row-sub">{q.patient_id} · {q.status}{q.est_wait_minutes !== null ? ` · ~${q.est_wait_minutes} min` : ''}</span>
                </div>
                <div className="ca-row-actions">
                  {NEXT_STATUS[q.status].map(({ status, icon: Icon }) => (
                    <button key={status} className="ca-btn ca-btn--sm ca-btn--secondary" onClick={() => advance(q.ticket_id, status)}>
                      <Icon size={14} />
                      {status.replace('_', ' ')}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
