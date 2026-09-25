import { useState } from 'react';
import { Stethoscope, AlertTriangle, Sparkles, ArrowRight } from 'lucide-react';
import { useI18n } from '../../i18n';
import { runTriage } from '../../services/api';
import { enqueueAction } from '../../services/offlineQueue';
import type { Patient, TriageResult, UrgencyBand } from '../../types/schema';

interface Props {
  patient: Patient;
  defaultActor: string;
  onTriaged: (result: TriageResult) => void;
  onCreateReferral: () => void;
}

const URGENCY_BADGE: Record<UrgencyBand, string> = {
  ROUTINE: 'ca-badge--routine',
  SOON: 'ca-badge--soon',
  URGENT: 'ca-badge--urgent',
  EMERGENCY: 'ca-badge--emergency',
};

export default function TriageScreen({ patient, defaultActor, onTriaged, onCreateReferral }: Props) {
  const { t } = useI18n();
  const [symptoms, setSymptoms] = useState('');
  const [actor, setActor] = useState(defaultActor);
  const [thinking, setThinking] = useState(false);
  const [result, setResult] = useState<TriageResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [queued, setQueued] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (thinking || !symptoms.trim()) return;
    setThinking(true);
    setError(null);
    setQueued(false);

    const input = { patient_id: patient.patient_id, symptoms_text: symptoms.trim(), actor: actor.trim() || defaultActor };

    try {
      const triage = await runTriage(input.patient_id, input.symptoms_text, input.actor);
      setResult(triage);
      onTriaged(triage);
    } catch (err) {
      if (!navigator.onLine) {
        await enqueueAction('TRIAGE_SUBMISSION', input);
        setQueued(true);
      } else {
        setError(err instanceof Error ? err.message : 'Triage request failed');
      }
    } finally {
      setThinking(false);
    }
  };

  return (
    <div className="ca-wrap animate-fade-in">
      <div className="card">
        <div className="flex-row items-center gap-3" style={{ marginBottom: '0.4rem' }}>
          <div style={{ background: '#ede9fe', color: '#7c3aed', padding: '0.6rem', borderRadius: '12px', display: 'flex' }}>
            <Stethoscope size={22} strokeWidth={2.2} />
          </div>
          <div>
            <h2 className="ca-section-title" style={{ marginBottom: 0 }}>{t('caTriageTitle')}</h2>
            <div className="ca-section-subtitle" style={{ marginBottom: 0 }}>
              {t('caTriageSubtitle')} — {patient.name}, {patient.age}
            </div>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="ca-form-grid ca-form-grid--single" style={{ marginTop: '1.25rem' }}>
          <div className="ca-field">
            <label htmlFor="ca-symptoms">{t('caSymptoms')} *</label>
            <textarea
              id="ca-symptoms"
              className="ca-textarea"
              placeholder={t('caSymptomsPlaceholder')}
              value={symptoms}
              onChange={(e) => setSymptoms(e.target.value)}
              required
            />
          </div>
          <div className="ca-field">
            <label htmlFor="ca-actor">{t('caActor')}</label>
            <input id="ca-actor" className="ca-input" value={actor} onChange={(e) => setActor(e.target.value)} />
          </div>
        </form>

        {error && (
          <div role="alert" style={{ marginTop: '1rem', padding: '0.75rem 1rem', borderRadius: '8px', fontWeight: 600, background: 'var(--color-reject-bg)', color: 'var(--color-reject)' }}>
            {error}
          </div>
        )}
        {queued && (
          <div role="status" style={{ marginTop: '1rem', padding: '0.75rem 1rem', borderRadius: '8px', fontWeight: 600, background: 'var(--color-hold-bg)', color: '#92400e' }}>
            {t('caOfflineQueuedAction')}
          </div>
        )}

        <div className="ca-actions-row">
          <button type="submit" className="ca-btn ca-btn--primary" onClick={handleSubmit} disabled={thinking || !symptoms.trim()}>
            <Sparkles size={16} />
            {thinking ? '…' : t('caRunTriage')}
          </button>
        </div>

        {thinking && (
          <div
            className="animate-pulse"
            style={{
              marginTop: '1.25rem', padding: '1rem 1.25rem', borderRadius: '12px',
              background: '#ede9fe', color: '#5b21b6', fontWeight: 600,
              display: 'flex', alignItems: 'center', gap: '0.6rem',
            }}
          >
            <Sparkles size={18} className="animate-pulse" />
            {t('caTriageThinking')}
          </div>
        )}
      </div>

      {result && (
        <div className="card animate-fade-in" style={{ marginTop: '1.25rem' }}>
          {result.urgency === 'EMERGENCY' && (
            <div className="ca-emergency-banner">
              <AlertTriangle size={26} strokeWidth={2.3} />
              {t('caEmergencyBanner')}
            </div>
          )}

          <div className="flex-row items-center gap-3" style={{ flexWrap: 'wrap', marginBottom: '1rem' }}>
            <span className={`ca-badge ${URGENCY_BADGE[result.urgency]}`}>{t('caUrgency')}: {result.urgency}</span>
            <span className="ca-badge ca-badge--info">{t('caSuggestedFacility')}: {result.suggested_facility_level.replace('_', ' ')}</span>
            <span className="ca-badge ca-badge--neutral">{t('caConfidence')}: {result.confidence.replace('_', ' ')}</span>
          </div>

          <div style={{ marginBottom: '1rem' }}>
            <div style={{ fontSize: '0.78rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--color-text-secondary)', marginBottom: '0.4rem' }}>
              {t('caReasoning')}
            </div>
            <p style={{ lineHeight: 1.6, fontSize: '0.95rem', whiteSpace: 'pre-wrap' }}>{result.reasoning}</p>
          </div>

          {result.evidence_sources.length > 0 && (
            <div style={{ marginBottom: '1rem' }}>
              <div style={{ fontSize: '0.78rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--color-text-secondary)', marginBottom: '0.5rem' }}>
                {t('caEvidence')}
              </div>
              <div className="flex-row gap-2" style={{ flexWrap: 'wrap' }}>
                {result.evidence_sources.map((src) => (
                  <span key={src} className="ca-badge ca-badge--neutral">{src}</span>
                ))}
              </div>
            </div>
          )}

          <div className="ca-actions-row">
            <button className="ca-btn ca-btn--primary" onClick={onCreateReferral}>
              {t('caCreateReferral')}
              <ArrowRight size={16} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
