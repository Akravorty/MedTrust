import { useState } from 'react';
import { UserPlus, Stethoscope, Route, FlaskConical, Ticket, Video, CalendarClock, LayoutDashboard, Check, User } from 'lucide-react';
import { useI18n } from '../../i18n';
import type { Key } from '../../i18n';
import type { Patient, TriageResult, Referral } from '../../types/schema';
import PatientRegistration from './PatientRegistration';
import TriageScreen from './TriageScreen';
import ReferralTracker from './ReferralTracker';
import QueueScreen from './QueueScreen';
import TeleconsultScreen from './TeleconsultScreen';
import FollowUpScreen from './FollowUpScreen';
import DiagnosticsScreen from './DiagnosticsScreen';
import FacilityDashboard from './FacilityDashboard';
import PatientTimeline from './PatientTimeline';

export type CaStep = 'REGISTER' | 'TRIAGE' | 'REFERRAL' | 'DIAGNOSTICS' | 'QUEUE' | 'TELECONSULT' | 'FOLLOWUP' | 'DASHBOARD';

const STEPS: { id: CaStep; icon: typeof UserPlus; labelKey: Key }[] = [
  { id: 'REGISTER', icon: UserPlus, labelKey: 'caRegisterTitle' },
  { id: 'TRIAGE', icon: Stethoscope, labelKey: 'caTriageTitle' },
  { id: 'REFERRAL', icon: Route, labelKey: 'caReferralTitle' },
  { id: 'DIAGNOSTICS', icon: FlaskConical, labelKey: 'caDiagnosticsTitle' },
  { id: 'QUEUE', icon: Ticket, labelKey: 'caQueueTitle' },
  { id: 'TELECONSULT', icon: Video, labelKey: 'caTeleconsultTitle' },
  { id: 'FOLLOWUP', icon: CalendarClock, labelKey: 'caFollowUpTitle' },
  { id: 'DASHBOARD', icon: LayoutDashboard, labelKey: 'caDashboardTitle' },
];

export default function CareAccessFlow() {
  const { t } = useI18n();
  const [step, setStep] = useState<CaStep>('REGISTER');
  const [patient, setPatient] = useState<Patient | null>(null);
  const [triage, setTriage] = useState<TriageResult | null>(null);
  const [referral, setReferral] = useState<Referral | null>(null);
  const [actorName, setActorName] = useState('ASHA-DEMO');

  const stepIdx = STEPS.findIndex((s) => s.id === step);

  const handleRegistered = (p: Patient) => {
    setPatient(p);
    setActorName(p.registered_by || actorName);
    setStep('TRIAGE');
  };

  const canVisit = (s: CaStep) => s === 'REGISTER' || s === 'DASHBOARD' || Boolean(patient);

  return (
    <div>
      {patient && (
        <div className="ca-patient-context-bar" style={{
          display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center',
          background: 'var(--kpi-card-bg)', border: '1px solid var(--kpi-card-border)', borderRadius: '12px',
          padding: '0.75rem 1rem', marginBottom: '1rem', gap: '1rem',
          boxShadow: '0 2px 4px rgba(0,0,0,0.02)'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <div style={{ background: '#e0f2fe', color: '#0284c7', padding: '0.4rem', borderRadius: '8px' }}>
              <User size={16} strokeWidth={2.5} />
            </div>
            <div>
              <div style={{ fontWeight: 700, color: 'var(--color-text-primary)' }}>{patient.name}</div>
              <div style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                {patient.age}y, {patient.gender} · ID: <span style={{ fontFamily: 'monospace' }}>{patient.patient_id}</span>
              </div>
            </div>
          </div>
          <div style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--color-text-secondary)', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            Current workflow:
            <span className="ca-badge ca-badge--info">{t(STEPS.find(s => s.id === step)?.labelKey || '')}</span>
          </div>
        </div>
      )}

      <div className="ca-stepper">
        {STEPS.map(({ id, icon: Icon, labelKey }, i) => (
          <button
            key={id}
            className={`ca-step ${step === id ? 'ca-step--active' : ''} ${i < stepIdx && patient ? 'ca-step--done' : ''}`}
            onClick={() => canVisit(id) && setStep(id)}
            disabled={!canVisit(id)}
          >
            <span className="ca-step-num">
              {i < stepIdx && patient ? <Check size={12} /> : <Icon size={12} />}
            </span>
            {t(labelKey)}
          </button>
        ))}
      </div>

      {step === 'REGISTER' && (
        <PatientRegistration defaultActor={actorName} onRegistered={handleRegistered} />
      )}

      {step === 'TRIAGE' && patient && (
        <>
          <TriageScreen
            patient={patient}
            defaultActor={actorName}
            onTriaged={setTriage}
            onCreateReferral={() => setStep('REFERRAL')}
          />
          <div className="ca-wrap"><PatientTimeline patient={patient} /></div>
        </>
      )}

      {step === 'REFERRAL' && patient && (
        <ReferralTracker
          patient={patient}
          triage={triage}
          defaultActor={actorName}
          onReferralReady={setReferral}
          onProceedToQueue={() => setStep('DIAGNOSTICS')}
        />
      )}

      {step === 'DIAGNOSTICS' && patient && (
        <DiagnosticsScreen patient={patient} defaultActor={actorName} onNext={() => setStep('QUEUE')} />
      )}

      {step === 'QUEUE' && patient && (
        <QueueScreen
          patient={patient}
          triage={triage}
          defaultActor={actorName}
          onProceedToTeleconsult={() => setStep('TELECONSULT')}
        />
      )}

      {step === 'TELECONSULT' && patient && (
        <TeleconsultScreen
          patient={patient}
          triage={triage}
          referral={referral}
          defaultActor={actorName}
          onCompleted={() => setStep('FOLLOWUP')}
        />
      )}

      {step === 'FOLLOWUP' && patient && (
        <FollowUpScreen patient={patient} defaultActor={actorName} onNext={() => setStep('DASHBOARD')} />
      )}

      {step === 'DASHBOARD' && (
        <FacilityDashboard defaultFacilityId={patient?.home_facility_id} />
      )}
    </div>
  );
}
