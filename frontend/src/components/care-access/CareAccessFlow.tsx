import { useState } from 'react';
import { UserPlus, Stethoscope, Route, Ticket, Video, CalendarClock, LayoutDashboard, Check } from 'lucide-react';
import { useI18n } from '../../i18n';
import type { Key } from '../../i18n';
import type { Patient, TriageResult, Referral } from '../../types/schema';
import PatientRegistration from './PatientRegistration';
import TriageScreen from './TriageScreen';
import ReferralTracker from './ReferralTracker';
import QueueScreen from './QueueScreen';
import TeleconsultScreen from './TeleconsultScreen';
import FollowUpScreen from './FollowUpScreen';
import FacilityDashboard from './FacilityDashboard';
import PatientTimeline from './PatientTimeline';

export type CaStep = 'REGISTER' | 'TRIAGE' | 'REFERRAL' | 'QUEUE' | 'TELECONSULT' | 'FOLLOWUP' | 'DASHBOARD';

const STEPS: { id: CaStep; icon: typeof UserPlus; labelKey: Key }[] = [
  { id: 'REGISTER', icon: UserPlus, labelKey: 'caRegisterTitle' },
  { id: 'TRIAGE', icon: Stethoscope, labelKey: 'caTriageTitle' },
  { id: 'REFERRAL', icon: Route, labelKey: 'caReferralTitle' },
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
          onProceedToQueue={() => setStep('QUEUE')}
        />
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
