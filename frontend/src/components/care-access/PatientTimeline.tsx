import { useEffect, useState } from 'react';
import { History, ShieldCheck } from 'lucide-react';
import { useI18n } from '../../i18n';
import { getPatientTimeline, verifyPatientRecord } from '../../services/api';
import type { Patient, PatientTimelineEvent, PatientVerification } from '../../types/schema';

interface Props {
  patient: Patient;
}

export default function PatientTimeline({ patient }: Props) {
  const { t } = useI18n();
  const [events, setEvents] = useState<PatientTimelineEvent[]>([]);
  const [verification, setVerification] = useState<PatientVerification | null>(null);

  useEffect(() => {
    getPatientTimeline(patient.patient_id).then((d) => setEvents(d.events)).catch(() => {});
    verifyPatientRecord(patient.patient_id).then(setVerification).catch(() => {});
  }, [patient.patient_id]);

  if (events.length === 0) return null;

  return (
    <div className="card" style={{ marginTop: '1.25rem' }}>
      <div className="flex-row items-center justify-between" style={{ marginBottom: '1rem' }}>
        <div className="flex-row items-center gap-2">
          <History size={18} strokeWidth={2.2} />
          <h3 style={{ fontSize: '0.98rem', fontWeight: 700 }}>{t('caTimelineTitle')}</h3>
        </div>
        {verification?.valid && (
          <span className="ca-badge ca-badge--routine">
            <ShieldCheck size={13} /> {t('caRecordVerified')}
          </span>
        )}
      </div>

      <div className="ca-timeline">
        {events.map((e) => (
          <div key={e.event_id} className="ca-timeline-item">
            <div className="ca-timeline-action">{e.action.replace(/_/g, ' ')}</div>
            <div className="ca-timeline-meta">{e.actor} · {new Date(e.timestamp).toLocaleString()}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
