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
          <h2 className="ca-section-title" style={{ marginBottom: 0 }}>{t('caQueueTitle')}</h2>
        </div>

        {!ticket ? (
          <div className="ca-actions-row">
            <button className="ca-btn ca-btn--primary" onClick={handleJoin} disabled={submitting}>
              <Ticket size={16} />
              {submitting ? '…' : t('caJoinQueue')}
            </button>
          </div>
        ) : (
          <div style={{ marginTop: '1rem' }}>
            <div className="flex-row items-center gap-4" style={{ flexWrap: 'wrap' }}>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: '2.4rem', fontWeight: 800, color: '#0284c7', lineHeight: 1 }}>#{ticket.token_number}</div>
                <div style={{ fontSize: '0.75rem', color: 'var(--color-text-secondary)', fontWeight: 600 }}>{t('caTokenNumber')}</div>
              </div>
              <span className={`ca-badge ${ticket.priority ? 'ca-badge--emergency' : 'ca-badge--info'}`}>{ticket.status}</span>
              {ticket.est_wait_minutes !== null && (
                <span className="ca-badge ca-badge--neutral"><Clock size={13} style={{ marginRight: 4 }} />~{ticket.est_wait_minutes} min</span>
              )}
            </div>
            {ticket.status === 'IN_CONSULT' && (
              <div className="ca-actions-row">
                <button className="ca-btn ca-btn--success" onClick={onProceedToTeleconsult}>
                  {t('caNext')} <ArrowRight size={14} />
                </button>
              </div>
            )}
          </div>
        )}

        {error && (
          <div role="alert" style={{ marginTop: '1rem', padding: '0.75rem 1rem', borderRadius: '8px', fontWeight: 600, background: 'var(--color-reject-bg)', color: 'var(--color-reject)' }}>
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
