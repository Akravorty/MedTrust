import { useState } from 'react';
import { UserPlus, WifiOff } from 'lucide-react';
import { useI18n } from '../../i18n';
import { useFacilities, facilityLabel } from '../../hooks/useFacilities';
import { registerPatient } from '../../services/api';
import { enqueueAction } from '../../services/offlineQueue';
import type { Patient } from '../../types/schema';

interface Props {
  defaultActor: string;
  onRegistered: (patient: Patient) => void;
}

export default function PatientRegistration({ defaultActor, onRegistered }: Props) {
  const { t } = useI18n();
  const { facilities, loading: facilitiesLoading } = useFacilities();

  const [name, setName] = useState('');
  const [age, setAge] = useState('');
  const [gender, setGender] = useState('Female');
  const [village, setVillage] = useState('');
  const [homeFacilityId, setHomeFacilityId] = useState('');
  const [registeredBy, setRegisteredBy] = useState(defaultActor);
  const [phone, setPhone] = useState('');

  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<{ kind: 'error' | 'info'; text: string } | null>(null);

  const facilityId = homeFacilityId || facilities[0]?.facility_id || '';

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    if (!name.trim() || !age || !village.trim() || !registeredBy.trim() || !facilityId) {
      setMessage({ kind: 'error', text: 'Please fill in all required fields.' });
      return;
    }
    setSubmitting(true);
    setMessage(null);

    const input = {
      name: name.trim(),
      age: Number(age),
      gender,
      village: village.trim(),
      home_facility_id: facilityId,
      registered_by: registeredBy.trim(),
      phone: phone.trim() || undefined,
    };

    try {
      const patient = await registerPatient(input);
      onRegistered(patient);
    } catch (err) {
      if (!navigator.onLine) {
        await enqueueAction('PATIENT_REGISTRATION', input);
        setMessage({ kind: 'info', text: t('caOfflineQueuedAction') });
      } else {
        const msg = err instanceof Error ? err.message : 'Unknown error';
        setMessage({ kind: 'error', text: `Could not register patient: ${msg}` });
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="card ca-wrap animate-fade-in">
      <div className="flex-row items-center gap-3" style={{ marginBottom: '0.4rem' }}>
        <div style={{ background: '#e0f2fe', color: '#0284c7', padding: '0.6rem', borderRadius: '12px', display: 'flex' }}>
          <UserPlus size={22} strokeWidth={2.2} />
        </div>
        <div>
          <h2 className="ca-section-title" style={{ marginBottom: 0 }}>{t('caRegisterTitle')}</h2>
          <div className="ca-section-subtitle" style={{ marginBottom: 0 }}>{t('caRegisterSubtitle')}</div>
        </div>
      </div>

      <form onSubmit={handleSubmit} className="ca-form-grid" style={{ marginTop: '1.25rem' }}>
        <div className="ca-field">
          <label htmlFor="ca-name">{t('caName')} *</label>
          <input id="ca-name" className="ca-input" value={name} onChange={(e) => setName(e.target.value)} required />
        </div>
        <div className="ca-field">
          <label htmlFor="ca-age">{t('caAge')} *</label>
          <input id="ca-age" type="number" min={0} max={120} className="ca-input" value={age} onChange={(e) => setAge(e.target.value)} required />
        </div>
        <div className="ca-field">
          <label htmlFor="ca-gender">{t('caGender')}</label>
          <select id="ca-gender" className="ca-select" value={gender} onChange={(e) => setGender(e.target.value)}>
            <option value="Female">Female</option>
            <option value="Male">Male</option>
            <option value="Other">Other</option>
          </select>
        </div>
        <div className="ca-field">
          <label htmlFor="ca-village">{t('caVillage')} *</label>
          <input id="ca-village" className="ca-input" value={village} onChange={(e) => setVillage(e.target.value)} required />
        </div>
        <div className="ca-field ca-field--span2">
          <label htmlFor="ca-facility">{t('caFacility')} *</label>
          <select
            id="ca-facility"
            className="ca-select"
            value={facilityId}
            onChange={(e) => setHomeFacilityId(e.target.value)}
            disabled={facilitiesLoading}
            required
          >
            {facilities.length === 0 && <option value="">{facilitiesLoading ? 'Loading…' : 'No facilities found'}</option>}
            {facilities.map((f) => (
              <option key={f.facility_id} value={f.facility_id}>{facilityLabel(f)} — {f.village_or_area}</option>
            ))}
          </select>
        </div>
        <div className="ca-field">
          <label htmlFor="ca-registered-by">{t('caRegisteredBy')} *</label>
          <input id="ca-registered-by" className="ca-input" value={registeredBy} onChange={(e) => setRegisteredBy(e.target.value)} required />
        </div>
        <div className="ca-field">
          <label htmlFor="ca-phone">{t('caPhone')}</label>
          <input id="ca-phone" className="ca-input" value={phone} onChange={(e) => setPhone(e.target.value)} />
        </div>
      </form>

      {message && (
        <div
          role="alert"
          style={{
            marginTop: '1rem', padding: '0.75rem 1rem', borderRadius: '8px', fontWeight: 600,
            display: 'flex', alignItems: 'center', gap: '0.5rem',
            background: message.kind === 'error' ? 'var(--color-reject-bg)' : 'var(--color-hold-bg)',
            color: message.kind === 'error' ? 'var(--color-reject)' : '#92400e',
          }}
        >
          {message.kind === 'info' && <WifiOff size={16} />}
          {message.text}
        </div>
      )}

      <div className="ca-actions-row">
        <button type="submit" className="ca-btn ca-btn--primary" onClick={handleSubmit} disabled={submitting}>
          {submitting ? t('caRegistering') : t('caSubmit')}
        </button>
      </div>
    </div>
  );
}
