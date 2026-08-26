import { AlertTriangle } from 'lucide-react';

interface Props {
  batchId: string;
  onRecallTriggered: () => void;
}

export default function RecallFlow({ batchId, onRecallTriggered }: Props) {
  return (
    <div className="card" style={{ border: '1px solid var(--color-recall)', backgroundColor: 'var(--color-recall-bg)' }}>
      <div className="flex-row items-center gap-4">
        <div style={{ color: 'var(--color-recall)' }}>
          <AlertTriangle size={32} />
        </div>
        <div style={{ flex: 1 }}>
          <h3 className="text-recall">Emergency Controls</h3>
          <p style={{ fontSize: '0.9rem', color: 'var(--color-text-secondary)', marginTop: '0.25rem' }}>
            Simulate a late-detected defect propagation for {batchId}.
          </p>
        </div>
        <button className="btn-danger" onClick={onRecallTriggered}>
          Simulate Defect & Recall
        </button>
      </div>
    </div>
  );
}
