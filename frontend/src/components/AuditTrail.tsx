import { useEffect, useState } from 'react';
import { TraceEvent } from '../types/schema';
import { getTrace, verifyChain } from '../services/api';
import { ShieldCheck, ArrowRight, Loader2 } from 'lucide-react';

// Collapses consecutive events that represent the same real decision
// logged more than once (e.g. re-evaluating a batch several times during
// a demo/rehearsal re-fires RISK_DECISION_RECORDED each time). Keeps the
// LATEST occurrence of each run, so the audit trail still shows the most
// recent decision, and only merges entries that are truly identical in
// both action and decision -- a genuine HOLD -> ACCEPT flip is never
// collapsed, since the decision values differ.
function dedupeConsecutive(events: TraceEvent[]): TraceEvent[] {
  const result: TraceEvent[] = [];
  for (const evt of events) {
    const prev = result[result.length - 1];
    if (prev && prev.action === evt.action && prev.decision === evt.decision) {
      result[result.length - 1] = evt; // replace with the later occurrence
    } else {
      result.push(evt);
    }
  }
  return result;
}

export default function AuditTrail({ batchId }: { batchId: string }) {
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [verifying, setVerifying] = useState(false);
  const [verified, setVerified] = useState(false);

  useEffect(() => {
    getTrace(batchId)
      .then((fetched) => setEvents(dedupeConsecutive(fetched)))
      .catch(() => setEvents([]));
  }, [batchId]);

  const handleVerify = async () => {
    setVerifying(true);
    const result = await verifyChain(batchId);
    setVerified(result);
    setVerifying(false);
  };

  return (
    <div className="card" style={{ overflowX: 'hidden' }}>
      <div className="flex-row justify-between items-center" style={{ marginBottom: '1.5rem' }}>
        <h3>Ledger Audit Trail</h3>
        {!verified ? (
          <button className="btn-primary flex-row items-center gap-2" onClick={handleVerify} disabled={verifying}>
            {verifying ? <Loader2 className="animate-spin" size={16} /> : <ShieldCheck size={16} />}
            {verifying ? 'Verifying Chain...' : 'Verify Chain'}
          </button>
        ) : (
          <span className="text-accept flex-row items-center gap-2" style={{ fontWeight: 600 }}>
            <ShieldCheck size={18} />
            Chain integrity verified
          </span>
        )}
      </div>

      <div className="flex-row gap-4" style={{ overflowX: 'auto', paddingBottom: '1rem', paddingRight: '260px' }}>
        {events.map((evt, idx) => (
          <div key={evt.id} className="flex-row items-center gap-4">
            <div className="flex-col" style={{ padding: '1rem', backgroundColor: 'var(--color-bg-primary)', borderRadius: '8px', minWidth: '150px' }}>
              <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)', marginBottom: '0.25rem' }}>
                {new Date(evt.timestamp).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
              </span>
              <strong style={{ display: 'block', marginBottom: '0.25rem' }}>{evt.action}</strong>
              <span style={{ fontSize: '0.9rem' }}>{evt.location}</span>
            </div>
            {idx < events.length - 1 && <ArrowRight color="var(--color-border)" />}
          </div>
        ))}
      </div>
    </div>
  );
}