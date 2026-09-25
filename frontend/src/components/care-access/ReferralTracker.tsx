import { useEffect, useState } from 'react';
import { Route, ArrowRight, Check, X, Truck, RotateCw } from 'lucide-react';
import { useI18n } from '../../i18n';
import type { Key } from '../../i18n';
import { useFacilities, facilityLabel } from '../../hooks/useFacilities';
import { createReferral, listReferrals, updateReferralStatus } from '../../services/api';
import type { Patient, TriageResult, Referral, ReferralStatus, UrgencyBand } from '../../types/schema';

interface Props {
  patient: Patient;
  triage: TriageResult | null;
  defaultActor: string;
  onReferralReady: (referral: Referral) => void;
  onProceedToQueue: () => void;
}

const STATUS_ORDER: ReferralStatus[] = ['CREATED', 'ACCEPTED', 'IN_TRANSIT', 'COMPLETED'];

const NEXT_STATUS: Record<ReferralStatus, { status: ReferralStatus; label: Key; icon: typeof Check }[]> = {
  CREATED: [{ status: 'ACCEPTED', label: 'caAccept', icon: Check }, { status: 'CANCELLED', label: 'caCancel', icon: X }],
  ACCEPTED: [{ status: 'IN_TRANSIT', label: 'caMarkInTransit', icon: Truck }, { status: 'CANCELLED', label: 'caCancel', icon: X }],
  IN_TRANSIT: [{ status: 'COMPLETED', label: 'caCompleteAction', icon: Check }, { status: 'CANCELLED', label: 'caCancel', icon: X }],
  COMPLETED: [],
  CANCELLED: [],
};

// Best-effort mapping so the "to" facility dropdown defaults to something
// plausible for the AI's suggested level — the user can still override it.
function suggestNext(level: string, facilityId: string, facilities: { facility_id: string; level: string }[]) {
  const same = facilities.find((f) => f.level === level && f.facility_id !== facilityId);
  return same?.facility_id ?? facilities.find((f) => f.facility_id !== facilityId)?.facility_id ?? '';
}

export default function ReferralTracker({ patient, triage, defaultActor, onReferralReady, onProceedToQueue }: Props) {
  const { t } = useI18n();
  const { facilities } = useFacilities();

  const [toFacilityId, setToFacilityId] = useState('');
  const [reason, setReason] = useState(triage?.reasoning?.slice(0, 240) ?? '');
  const [urgency, setUrgency] = useState<UrgencyBand>(triage?.urgency ?? 'ROUTINE');
  const [createdBy, setCreatedBy] = useState(defaultActor);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [referral, setReferral] = useState<Referral | null>(null);
  const [incoming, setIncoming] = useState<Referral[]>([]);
  const [transitioning, setTransitioning] = useState(false);

  useEffect(() => {
    if (facilities.length && !toFacilityId) {
      setToFacilityId(suggestNext(triage?.suggested_facility_level ?? '', patient.home_facility_id, facilities));
    }
  }, [facilities, triage, patient.home_facility_id, toFacilityId]);

  useEffect(() => {
    listReferrals({ patient_id: patient.patient_id }).then((refs) => {
      if (refs.length) setReferral(refs[refs.length - 1]);
    }).catch(() => {});
  }, [patient.patient_id]);

  useEffect(() => {
    if (!referral) return;
    listReferrals({ to_facility_id: referral.to_facility_id }).then(setIncoming).catch(() => {});
  }, [referral]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting || !toFacilityId || !reason.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const created = await createReferral({
        patient_id: patient.patient_id,
        from_facility_id: patient.home_facility_id,
        to_facility_id: toFacilityId,
        reason: reason.trim(),
        urgency,
        created_by: createdBy.trim() || defaultActor,
      });
      setReferral(created);
      onReferralReady(created);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create referral');
    } finally {
      setSubmitting(false);
    }
  };

  const advance = async (status: ReferralStatus) => {
    if (!referral || transitioning) return;
    setTransitioning(true);
    setError(null);
    try {
      const updated = await updateReferralStatus(referral.referral_id, status, createdBy.trim() || defaultActor);
      setReferral(updated);
      onReferralReady(updated);
    } catch (err) {
      // 409 = invalid transition; surface it plainly rather than silently failing
      setError(err instanceof Error ? err.message : 'Could not update referral status');
    } finally {
      setTransitioning(false);
    }
  };

  return (
    <div className="ca-wrap animate-fade-in">
      <div className="card">
        <div className="flex-row items-center gap-3" style={{ marginBottom: '0.4rem' }}>
          <div style={{ background: '#fef3c7', color: '#b45309', padding: '0.6rem', borderRadius: '12px', display: 'flex' }}>
            <Route size={22} strokeWidth={2.2} />
          </div>
          <h2 className="ca-section-title" style={{ marginBottom: 0 }}>{t('caReferralTitle')}</h2>
        </div>

        {!referral ? (
          <>
            <form onSubmit={handleSubmit} className="ca-form-grid" style={{ marginTop: '1.25rem' }}>
              <div className="ca-field">
                <label>{t('caFrom')}</label>
                <input className="ca-input" value={facilities.find((f) => f.facility_id === patient.home_facility_id)?.name ?? patient.home_facility_id} disabled />
              </div>
              <div className="ca-field">
                <label htmlFor="ca-to-facility">{t('caTo')} *</label>
                <select id="ca-to-facility" className="ca-select" value={toFacilityId} onChange={(e) => setToFacilityId(e.target.value)} required>
                  <option value="">—</option>
                  {facilities.filter((f) => f.facility_id !== patient.home_facility_id).map((f) => (
                    <option key={f.facility_id} value={f.facility_id}>{facilityLabel(f)} — {f.village_or_area}</option>
                  ))}
                </select>
              </div>
              <div className="ca-field ca-field--span2">
                <label htmlFor="ca-reason">{t('caReason')} *</label>
                <textarea id="ca-reason" className="ca-textarea" value={reason} onChange={(e) => setReason(e.target.value)} required />
              </div>
              <div className="ca-field">
                <label htmlFor="ca-urgency">{t('caUrgency')}</label>
                <select id="ca-urgency" className="ca-select" value={urgency} onChange={(e) => setUrgency(e.target.value as UrgencyBand)}>
                  <option value="ROUTINE">ROUTINE</option>
                  <option value="SOON">SOON</option>
                  <option value="URGENT">URGENT</option>
                  <option value="EMERGENCY">EMERGENCY</option>
                </select>
              </div>
              <div className="ca-field">
                <label htmlFor="ca-created-by">{t('caCreatedBy')}</label>
                <input id="ca-created-by" className="ca-input" value={createdBy} onChange={(e) => setCreatedBy(e.target.value)} />
              </div>
            </form>
            <div className="ca-actions-row">
              <button className="ca-btn ca-btn--primary" onClick={handleSubmit} disabled={submitting || !toFacilityId || !reason.trim()}>
                {submitting ? '…' : t('caCreateReferral')}
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="ca-status-track">
              {STATUS_ORDER.map((s, i) => {
                const currentIdx = STATUS_ORDER.indexOf(referral.status);
                const cls = referral.status === 'CANCELLED'
                  ? 'ca-status-pill--cancelled'
                  : i < currentIdx ? 'ca-status-pill--past' : i === currentIdx ? 'ca-status-pill--active' : '';
                return (
                  <span key={s} className="flex-row items-center gap-2">
                    <span className={`ca-status-pill ${cls}`}>{s.replace('_', ' ')}</span>
                    {i < STATUS_ORDER.length - 1 && <span className="ca-status-arrow"><ArrowRight size={14} /></span>}
                  </span>
                );
              })}
              {referral.status === 'CANCELLED' && <span className="ca-status-pill ca-status-pill--cancelled">CANCELLED</span>}
            </div>

            <div className="ca-row-sub" style={{ marginBottom: '0.75rem' }}>
              {referral.reason} · <span className="ca-badge ca-badge--neutral" style={{ marginLeft: '0.3rem' }}>{referral.urgency}</span>
            </div>

            <div className="ca-actions-row">
              {NEXT_STATUS[referral.status].map(({ status, label, icon: Icon }) => (
                <button
                  key={status}
                  className={`ca-btn ca-btn--sm ${status === 'CANCELLED' ? 'ca-btn--danger' : 'ca-btn--primary'}`}
                  onClick={() => advance(status)}
                  disabled={transitioning}
                >
                  <Icon size={14} />
                  {t(label)}
                </button>
              ))}
              {referral.status === 'ACCEPTED' && (
                <button className="ca-btn ca-btn--sm ca-btn--success" onClick={onProceedToQueue}>
                  {t('caNext')} <ArrowRight size={14} />
                </button>
              )}
            </div>
          </>
        )}

        {error && (
          <div role="alert" style={{ marginTop: '1rem', padding: '0.75rem 1rem', borderRadius: '8px', fontWeight: 600, background: 'var(--color-reject-bg)', color: 'var(--color-reject)' }}>
            {error}
          </div>
        )}
      </div>

      {referral && incoming.length > 0 && (
        <div className="card" style={{ marginTop: '1.25rem' }}>
          <div className="flex-row items-center justify-between" style={{ marginBottom: '0.85rem' }}>
            <h3 style={{ fontSize: '0.98rem', fontWeight: 700 }}>{t('caIncomingReferrals')}</h3>
            <button className="ca-btn ca-btn--sm ca-btn--secondary" onClick={() => listReferrals({ to_facility_id: referral.to_facility_id }).then(setIncoming)}>
              <RotateCw size={14} />
            </button>
          </div>
          <div className="ca-list">
            {incoming.map((r) => (
              <div key={r.referral_id} className="ca-row">
                <div className="ca-row-main">
                  <span className="ca-row-title">{r.patient_id}</span>
                  <span className="ca-row-sub">{r.reason}</span>
                </div>
                <span className={`ca-badge ${r.status === 'CANCELLED' ? 'ca-badge--emergency' : 'ca-badge--info'}`}>{r.status}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
