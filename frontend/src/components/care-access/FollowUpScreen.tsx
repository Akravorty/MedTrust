import { useEffect, useState } from 'react';
import { CalendarClock, Check, Plus, ArrowRight } from 'lucide-react';
import { useI18n } from '../../i18n';
import { listFollowUps, completeFollowUp, createFollowUp } from '../../services/api';
import type { Patient, FollowUp, RiskCategory } from '../../types/schema';

interface Props {
  patient: Patient;
  defaultActor: string;
  onNext: () => void;
}

function isOverdue(dueDate: string): boolean {
  return new Date(dueDate) < new Date(new Date().toDateString());
}

export default function FollowUpScreen({ patient, defaultActor, onNext }: Props) {
  const { t } = useI18n();
  const [followUps, setFollowUps] = useState<FollowUp[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [riskCategory, setRiskCategory] = useState<RiskCategory>(patient.risk_category !== 'NONE' ? patient.risk_category : 'CHRONIC');
  const [reason, setReason] = useState('');
  const [dueDate, setDueDate] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = () => {
    setLoading(true);
    listFollowUps({ patient_id: patient.patient_id }).then(setFollowUps).catch(() => {}).finally(() => setLoading(false));
  };

  useEffect(refresh, [patient.patient_id]);

  const handleComplete = async (id: string) => {
    try {
      await completeFollowUp(id, defaultActor);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not complete follow-up');
    }
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy || !reason.trim() || !dueDate) return;
    setBusy(true);
    setError(null);
    try {
      await createFollowUp({
        patient_id: patient.patient_id,
        risk_category: riskCategory,
        reason: reason.trim(),
        due_date: dueDate,
        created_by: defaultActor,
      });
      setReason('');
      setDueDate('');
      setShowForm(false);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create follow-up');
    } finally {
      setBusy(false);
    }
  };

  const open = followUps.filter((f) => !f.completed);

  return (
    <div className="ca-wrap animate-fade-in">
      <div className="card">
        <div className="flex-row items-center justify-between" style={{ marginBottom: '1rem', flexWrap: 'wrap', gap: '0.75rem' }}>
          <div className="flex-row items-center gap-3">
            <div style={{ background: '#fee2e2', color: '#b91c1c', padding: '0.6rem', borderRadius: '12px', display: 'flex' }}>
              <CalendarClock size={22} strokeWidth={2.2} />
            </div>
            <h2 className="ca-section-title" style={{ marginBottom: 0 }}>{t('caFollowUpTitle')}</h2>
          </div>
          <button className="ca-btn ca-btn--sm ca-btn--secondary" onClick={() => setShowForm((s) => !s)}>
            <Plus size={14} /> {t('caScheduleFollowUp')}
          </button>
        </div>

        {showForm && (
          <form onSubmit={handleCreate} className="ca-form-grid" style={{ marginBottom: '1.25rem' }}>
            <div className="ca-field">
              <label htmlFor="ca-risk-category">{t('caRiskCategory')}</label>
              <select id="ca-risk-category" className="ca-select" value={riskCategory} onChange={(e) => setRiskCategory(e.target.value as RiskCategory)}>
                <option value="MATERNAL">MATERNAL</option>
                <option value="CHILD">CHILD</option>
                <option value="CHRONIC">CHRONIC</option>
              </select>
            </div>
            <div className="ca-field">
              <label htmlFor="ca-due-date">{t('caDueDate')} *</label>
              <input id="ca-due-date" type="date" className="ca-input" value={dueDate} onChange={(e) => setDueDate(e.target.value)} required />
            </div>
            <div className="ca-field ca-field--span2">
              <label htmlFor="ca-fu-reason">{t('caReason')} *</label>
              <input id="ca-fu-reason" className="ca-input" value={reason} onChange={(e) => setReason(e.target.value)} required />
            </div>
            <div className="ca-field--span2">
              <button type="submit" className="ca-btn ca-btn--primary" disabled={busy || !reason.trim() || !dueDate}>
                {busy ? '…' : t('caScheduleFollowUp')}
              </button>
            </div>
          </form>
        )}

        {loading ? (
          <div className="ca-empty-state">{t('caLoading')}</div>
        ) : open.length === 0 ? (
          <div className="ca-empty-state">No open follow-ups for this patient.</div>
        ) : (
          <div className="ca-list">
            {open.map((f) => {
              const overdue = isOverdue(f.due_date);
              return (
                <div key={f.follow_up_id} className={`ca-row ${overdue ? 'ca-row--overdue' : ''}`}>
                  <div className="ca-row-main">
                    <span className="ca-row-title">
                      {f.risk_category} — {f.reason}
                      {overdue && <span className="ca-badge ca-badge--emergency" style={{ marginLeft: 8 }}>{t('caOverdue')}</span>}
                    </span>
                    <span className="ca-row-sub">{t('caDueDate')}: {f.due_date}</span>
                  </div>
                  <button className="ca-btn ca-btn--sm ca-btn--success" onClick={() => handleComplete(f.follow_up_id)}>
                    <Check size={14} /> {t('caMarkComplete')}
                  </button>
                </div>
              );
            })}
          </div>
        )}

        {error && (
          <div role="alert" style={{ marginTop: '1rem', padding: '0.75rem 1rem', borderRadius: '8px', fontWeight: 600, background: 'var(--color-reject-bg)', color: 'var(--color-reject)' }}>
            {error}
          </div>
        )}

        <div className="ca-actions-row">
          <button className="ca-btn ca-btn--secondary" onClick={onNext}>
            {t('caDashboardTitle')} <ArrowRight size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}
