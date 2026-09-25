import { useEffect, useState } from 'react';
import { FlaskConical, Plus, ArrowRight, Beaker, ClipboardCheck, Eye, X } from 'lucide-react';
import { useI18n } from '../../i18n';
import {
  listDiagnosticOrders, createDiagnosticOrder, markSampleCollected,
  recordDiagnosticResult, reviewDiagnosticResult, cancelDiagnosticOrder,
} from '../../services/api';
import type { Patient, DiagnosticOrder, DiagnosticResultFlag } from '../../types/schema';

interface Props {
  patient: Patient;
  defaultActor: string;
  onNext: () => void;
}

const RESULT_FLAG_COLOR: Record<DiagnosticResultFlag, string> = {
  NORMAL: '#10b981',
  ABNORMAL: '#f59e0b',
  CRITICAL: '#dc2626',
};

export default function DiagnosticsScreen({ patient, defaultActor, onNext }: Props) {
  const { t } = useI18n();
  const [orders, setOrders] = useState<DiagnosticOrder[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [testType, setTestType] = useState('');
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [resultDraft, setResultDraft] = useState<Record<string, { flag: DiagnosticResultFlag; summary: string }>>({});

  const refresh = () => {
    setLoading(true);
    listDiagnosticOrders({ patient_id: patient.patient_id }).then(setOrders).catch(() => {}).finally(() => setLoading(false));
  };

  useEffect(refresh, [patient.patient_id]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy || !testType.trim() || !reason.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await createDiagnosticOrder({
        patient_id: patient.patient_id,
        ordering_facility_id: patient.home_facility_id,
        test_type: testType.trim(),
        reason: reason.trim(),
        ordered_by: defaultActor,
      });
      setTestType('');
      setReason('');
      setShowForm(false);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not order test');
    } finally {
      setBusy(false);
    }
  };

  const withErrorHandling = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Action failed');
    }
  };

  const openOrders = orders.filter((o) => o.status !== 'REVIEWED' && o.status !== 'CANCELLED');
  const closedOrders = orders.filter((o) => o.status === 'REVIEWED' || o.status === 'CANCELLED');

  return (
    <div className="ca-wrap animate-fade-in">
      <div className="card">
        <div className="flex-row items-center justify-between" style={{ marginBottom: '1rem', flexWrap: 'wrap', gap: '0.75rem' }}>
          <div className="flex-row items-center gap-3">
            <div style={{ background: '#ede9fe', color: '#7c3aed', padding: '0.6rem', borderRadius: '12px', display: 'flex' }}>
              <FlaskConical size={22} strokeWidth={2.2} />
            </div>
            <h2 className="ca-section-title" style={{ marginBottom: 0 }}>{t('caDiagnosticsTitle')}</h2>
          </div>
          <button className="ca-btn ca-btn--sm ca-btn--secondary" onClick={() => setShowForm((s) => !s)}>
            <Plus size={14} /> {t('caOrderTest')}
          </button>
        </div>

        {showForm && (
          <form onSubmit={handleCreate} className="ca-form-grid" style={{ marginBottom: '1.25rem' }}>
            <div className="ca-field">
              <label htmlFor="ca-test-type">{t('caTestType')} *</label>
              <input
                id="ca-test-type" className="ca-input" value={testType}
                onChange={(e) => setTestType(e.target.value)}
                placeholder={t('caTestTypePlaceholder')} required
              />
            </div>
            <div className="ca-field ca-field--span2">
              <label htmlFor="ca-dx-reason">{t('caReason')} *</label>
              <input id="ca-dx-reason" className="ca-input" value={reason} onChange={(e) => setReason(e.target.value)} required />
            </div>
            <div className="ca-field--span2">
              <button type="submit" className="ca-btn ca-btn--primary" disabled={busy || !testType.trim() || !reason.trim()}>
                {busy ? '…' : t('caOrderTest')}
              </button>
            </div>
          </form>
        )}

        {loading ? (
          <div className="ca-empty-state">{t('caLoading')}</div>
        ) : openOrders.length === 0 ? (
          <div className="ca-empty-state">{t('caNoOpenDiagnostics')}</div>
        ) : (
          <div className="ca-list">
            {openOrders.map((o) => {
              const draft = resultDraft[o.diagnostic_id] ?? { flag: 'NORMAL' as DiagnosticResultFlag, summary: '' };
              return (
                <div key={o.diagnostic_id} className="ca-row" style={{ flexDirection: 'column', alignItems: 'stretch', gap: '0.5rem' }}>
                  <div className="flex-row items-center justify-between" style={{ flexWrap: 'wrap', gap: '0.5rem' }}>
                    <div className="ca-row-main">
                      <span className="ca-row-title">{o.test_type} — {o.reason}</span>
                      <span className="ca-row-sub">
                        {t('caPerformingFacility')}: {o.performing_facility_id}
                        {o.routed && <em style={{ marginLeft: 6 }}>({t('caRoutedNotice')})</em>}
                        {' · '}{o.status}
                      </span>
                    </div>
                    <button
                      className="ca-btn ca-btn--sm ca-btn--secondary"
                      onClick={() => withErrorHandling(() => cancelDiagnosticOrder(o.diagnostic_id, defaultActor))}
                      title={t('caCancelOrder')}
                    >
                      <X size={14} />
                    </button>
                  </div>

                  {o.status === 'ORDERED' && (
                    <button
                      className="ca-btn ca-btn--sm ca-btn--success"
                      onClick={() => withErrorHandling(() => markSampleCollected(o.diagnostic_id, defaultActor))}
                    >
                      <Beaker size={14} /> {t('caSampleCollected')}
                    </button>
                  )}

                  {o.status === 'SAMPLE_COLLECTED' && (
                    <div className="flex-row items-center gap-2" style={{ flexWrap: 'wrap' }}>
                      <select
                        className="ca-select"
                        value={draft.flag}
                        onChange={(e) => setResultDraft((prev) => ({ ...prev, [o.diagnostic_id]: { ...draft, flag: e.target.value as DiagnosticResultFlag } }))}
                      >
                        <option value="NORMAL">NORMAL</option>
                        <option value="ABNORMAL">ABNORMAL</option>
                        <option value="CRITICAL">CRITICAL</option>
                      </select>
                      <input
                        className="ca-input" placeholder={t('caResultSummary')}
                        value={draft.summary}
                        onChange={(e) => setResultDraft((prev) => ({ ...prev, [o.diagnostic_id]: { ...draft, summary: e.target.value } }))}
                        style={{ minWidth: '200px', flex: 1 }}
                      />
                      <button
                        className="ca-btn ca-btn--sm ca-btn--primary"
                        disabled={!draft.summary.trim()}
                        onClick={() => withErrorHandling(() => recordDiagnosticResult(o.diagnostic_id, draft.flag, draft.summary.trim(), defaultActor))}
                      >
                        <ClipboardCheck size={14} /> {t('caRecordResult')}
                      </button>
                    </div>
                  )}

                  {o.status === 'RESULT_AVAILABLE' && (
                    <div className="flex-row items-center justify-between" style={{ flexWrap: 'wrap', gap: '0.5rem' }}>
                      <span>
                        <strong style={{ color: o.result_flag ? RESULT_FLAG_COLOR[o.result_flag] : undefined }}>{o.result_flag}</strong>
                        {' — '}{o.result_summary}
                      </span>
                      <button
                        className="ca-btn ca-btn--sm ca-btn--success"
                        onClick={() => withErrorHandling(() => reviewDiagnosticResult(o.diagnostic_id, defaultActor))}
                      >
                        <Eye size={14} /> {t('caReviewResult')}
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {closedOrders.length > 0 && (
          <div className="ca-list" style={{ marginTop: '1rem', opacity: 0.7 }}>
            {closedOrders.map((o) => (
              <div key={o.diagnostic_id} className="ca-row">
                <div className="ca-row-main">
                  <span className="ca-row-title">{o.test_type} — {o.reason}</span>
                  <span className="ca-row-sub">
                    {o.status}
                    {o.result_flag && ` · ${o.result_flag}`}
                    {o.result_summary && ` — ${o.result_summary}`}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}

        {error && (
          <div role="alert" style={{ marginTop: '1rem', padding: '0.75rem 1rem', borderRadius: '8px', fontWeight: 600, background: 'var(--color-reject-bg)', color: 'var(--color-reject)' }}>
            {error}
          </div>
        )}

        <div className="ca-actions-row">
          <button className="ca-btn ca-btn--secondary" onClick={onNext}>
            {t('caQueueTitle')} <ArrowRight size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}
