import { useEffect, useRef, useState } from 'react';
import { Video, PhoneCall, PhoneOff, Clock, FileText, ArrowRight, ExternalLink, Copy } from 'lucide-react';
import { useI18n } from '../../i18n';
import { scheduleTeleconsult, startTeleconsult, completeTeleconsult } from '../../services/api';
import { videoRoomUrl } from '../../services/video';
import type { Patient, TriageResult, Referral, TeleconsultSession } from '../../types/schema';

interface Props {
  patient: Patient;
  triage: TriageResult | null;
  referral: Referral | null;
  defaultActor: string;
  onCompleted: () => void;
}

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60).toString().padStart(2, '0');
  const s = (seconds % 60).toString().padStart(2, '0');
  return `${m}:${s}`;
}

export default function TeleconsultScreen({ patient, triage, referral, defaultActor, onCompleted }: Props) {
  const { t } = useI18n();
  const [doctorName, setDoctorName] = useState('');
  const [session, setSession] = useState<TeleconsultSession | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [notes, setNotes] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [linkCopied, setLinkCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (session?.status === 'ACTIVE') {
      timerRef.current = setInterval(() => setElapsed((e) => e + 1), 1000);
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [session?.status]);

  const handleSchedule = async () => {
    if (busy || !doctorName.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const s = await scheduleTeleconsult(patient.patient_id, patient.home_facility_id, doctorName.trim(), referral?.referral_id);
      setSession(s);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not schedule session');
    } finally {
      setBusy(false);
    }
  };

  const handleStart = async () => {
    if (!session || busy) return;
    setBusy(true);
    setError(null);
    try {
      setSession(await startTeleconsult(session.session_id));
      setElapsed(0);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start call');
    } finally {
      setBusy(false);
    }
  };

  const handleCopyLink = async () => {
    if (!session) return;
    try {
      await navigator.clipboard.writeText(videoRoomUrl(session.session_id));
      setLinkCopied(true);
      setTimeout(() => setLinkCopied(false), 2000);
    } catch {
      setError('Could not copy the link. Long-press or right-click "Open video room" to copy it.');
    }
  };

  const handleComplete = async () => {
    if (!session || busy) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await completeTeleconsult(session.session_id, notes.trim() || 'No notes recorded.');
      setSession(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not complete session');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="ca-wrap ca-wrap--wide animate-fade-in">
      <div className="flex-row gap-6" style={{ alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <div className="card" style={{ flex: '1 1 380px' }}>
          <div className="flex-row items-center gap-3" style={{ marginBottom: '1rem' }}>
            <div style={{ background: '#e0f2fe', color: '#0284c7', padding: '0.6rem', borderRadius: '12px', display: 'flex' }}>
              <Video size={22} strokeWidth={2.2} />
            </div>
            <h2 className="ca-section-title" style={{ marginBottom: 0 }}>{t('caTeleconsultTitle')}</h2>
          </div>

          <div className={`ca-video-frame ${session?.status === 'ACTIVE' ? 'ca-video-frame--active' : ''}`}>
            {session?.status === 'ACTIVE' && (
              <div className="ca-video-timer"><Clock size={13} />{formatElapsed(elapsed)}</div>
            )}
            <div className="ca-video-avatar">{(session?.doctor_name || doctorName || 'Dr').charAt(0).toUpperCase()}</div>
            <div style={{ fontWeight: 700 }}>{session?.doctor_name || doctorName || t('caDoctorName')}</div>
            <div style={{ fontSize: '0.8rem', opacity: 0.75 }}>
              {!session && 'Not scheduled'}
              {session?.status === 'SCHEDULED' && 'Scheduled — ready to start'}
              {session?.status === 'ACTIVE' && 'Live'}
              {session?.status === 'COMPLETED' && 'Call ended'}
            </div>
          </div>

          {!session && (
            <div className="ca-form-grid ca-form-grid--single" style={{ marginTop: '1.25rem' }}>
              <div className="ca-field">
                <label htmlFor="ca-doctor-name">{t('caDoctorName')} *</label>
                <input id="ca-doctor-name" className="ca-input" value={doctorName} onChange={(e) => setDoctorName(e.target.value)} />
              </div>
            </div>
          )}

          <div className="ca-actions-row">
            {!session && (
              <button className="ca-btn ca-btn--primary" onClick={handleSchedule} disabled={busy || !doctorName.trim()}>
                {t('caSchedule')}
              </button>
            )}
            {session?.status === 'SCHEDULED' && (
              <button className="ca-btn ca-btn--success" onClick={handleStart} disabled={busy}>
                <PhoneCall size={16} /> {t('caStartCall')}
              </button>
            )}
            {session?.status === 'ACTIVE' && (
              <button className="ca-btn ca-btn--danger" onClick={handleComplete} disabled={busy}>
                <PhoneOff size={16} /> {t('caEndCall')}
              </button>
            )}
            {session?.status === 'COMPLETED' && (
              <button className="ca-btn ca-btn--success" onClick={onCompleted}>
                {t('caNext')} <ArrowRight size={14} />
              </button>
            )}
          </div>

          {session && (session.status === 'SCHEDULED' || session.status === 'ACTIVE') && (
            <div className="ca-actions-row" style={{ marginTop: '0.75rem' }}>
              <a
                className="ca-btn ca-btn--sm ca-btn--secondary"
                href={videoRoomUrl(session.session_id)}
                target="_blank"
                rel="noopener noreferrer"
                style={{ textDecoration: 'none' }}
              >
                <ExternalLink size={14} /> {t('caJoinVideo')}
              </a>
              <button className="ca-btn ca-btn--sm ca-btn--secondary" onClick={handleCopyLink}>
                <Copy size={14} /> {linkCopied ? t('caVideoLinkCopied') : t('caCopyVideoLink')}
              </button>
            </div>
          )}

          {(session?.status === 'ACTIVE' || session?.status === 'COMPLETED') && (
            <div className="ca-form-grid ca-form-grid--single" style={{ marginTop: '1rem' }}>
              <div className="ca-field">
                <label htmlFor="ca-notes">{t('caNotes')}</label>
                <textarea
                  id="ca-notes"
                  className="ca-textarea"
                  value={session.notes ?? notes}
                  onChange={(e) => setNotes(e.target.value)}
                  disabled={session.status === 'COMPLETED'}
                />
              </div>
              {session.status === 'ACTIVE' && (
                <button className="ca-btn ca-btn--sm ca-btn--secondary" onClick={handleComplete} disabled={busy} style={{ alignSelf: 'flex-start' }}>
                  <FileText size={14} /> {t('caCompleteSession')}
                </button>
              )}
            </div>
          )}

          {error && (
            <div role="alert" style={{ marginTop: '1rem', padding: '0.75rem 1rem', borderRadius: '8px', fontWeight: 600, background: 'var(--color-reject-bg)', color: 'var(--color-reject)' }}>
              {error}
            </div>
          )}
        </div>

        <div className="card" style={{ flex: '1 1 320px', position: 'sticky', top: '5.5rem' }}>
          <h3 style={{ fontSize: '0.98rem', fontWeight: 700, marginBottom: '1rem' }}>{t('caPatientRecord')}</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', fontSize: '0.9rem' }}>
            <div><strong>{patient.name}</strong>, {patient.age} · {patient.gender}</div>
            <div style={{ color: 'var(--color-text-secondary)' }}>{patient.village}</div>
            {patient.risk_category !== 'NONE' && (
              <span className="ca-badge ca-badge--soon" style={{ alignSelf: 'flex-start' }}>{patient.risk_category}</span>
            )}
          </div>

          {triage && (
            <div style={{ marginTop: '1.1rem', paddingTop: '1.1rem', borderTop: '1px solid var(--kpi-card-border)' }}>
              <div className="flex-row items-center gap-2" style={{ marginBottom: '0.5rem' }}>
                <span className={`ca-badge ${
                  triage.urgency === 'EMERGENCY' ? 'ca-badge--emergency'
                  : triage.urgency === 'URGENT' ? 'ca-badge--urgent'
                  : triage.urgency === 'SOON' ? 'ca-badge--soon' : 'ca-badge--routine'
                }`}>{triage.urgency}</span>
              </div>
              <div style={{ fontSize: '0.85rem', color: 'var(--color-text-secondary)', marginBottom: '0.3rem' }}>{triage.symptoms_text}</div>
              <p style={{ fontSize: '0.88rem', lineHeight: 1.55 }}>{triage.reasoning}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
