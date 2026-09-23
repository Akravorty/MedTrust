import { useState, useEffect, useCallback } from 'react';
import ScanIntake from './components/ScanIntake';
import DecisionScreen from './components/DecisionScreen';
import ChatPanel from './components/ChatPanel';
import AuditTrail from './components/AuditTrail';
import RecallFlow from './components/RecallFlow';
import DemoToggle from './components/DemoToggle';
import ActivityTicker from './components/ActivityTicker';
import type { BatchDecision } from './types/schema';
import { scanBatch, simulateRecall, getAlerts } from './services/api';
import type { AlertRecord } from './services/api';
import { enqueueScan, replayQueue, onReconnect, queueDepth } from './services/offlineQueue';
import { Activity, CheckCircle2, AlertTriangle, Moon, Sun, AlertOctagon, RotateCcw, ThermometerSnowflake, Boxes, Clock, Tag, WifiOff } from 'lucide-react';

export type AppState = 'SCANNING' | 'DECISION' | 'RECALLED';

/* ── KPI mock data ── */
const KPI_DATA = [
  {
    id: 'batches',
    label: 'Batches Scanned Today',
    value: '142',
    sub: '+18 since 9 AM',
    Icon: Activity,
    accent: '#0ea5e9',
    accentBg: '#e0f2fe',
  },
  {
    id: 'passed',
    label: 'Quality Passed',
    value: '91.5%',
    sub: '130 / 142 batches',
    Icon: CheckCircle2,
    accent: '#10b981',
    accentBg: '#d1fae5',
  },
  {
    id: 'flagged',
    label: 'Flagged / On Hold',
    value: '12',
    sub: '8 Hold · 4 Reject',
    Icon: AlertTriangle,
    accent: '#f59e0b',
    accentBg: '#fef3c7',
  },
];

function KpiBar() {
  return (
    <div className="kpi-bar">
      {KPI_DATA.map(({ id, label, value, sub, Icon, accent, accentBg }) => (
        <div key={id} className="kpi-card">
          <div className="kpi-top-accent" style={{ backgroundColor: accent }} />
          <div className="kpi-inner">
            <div className="kpi-icon-wrap" style={{ backgroundColor: accentBg }}>
              <Icon size={18} color={accent} strokeWidth={2} />
            </div>
            <div className="kpi-text">
              <span className="kpi-value" style={{ color: accent }}>{value}</span>
              <span className="kpi-label">{label}</span>
              <span className="kpi-sub">{sub}</span>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function App() {
  const [appState, setAppState] = useState<AppState>('SCANNING');
  const [batchId, setBatchId] = useState<string | null>(null);
  const [decision, setDecision] = useState<BatchDecision | null>(null);
  const [isDark, setIsDark] = useState(false);
  const [pendingCount, setPendingCount] = useState(0);
  const [isEvaluating, setIsEvaluating] = useState(false);
  const [scanMessage, setScanMessage] = useState<{ kind: 'error' | 'info'; text: string } | null>(null);
  const [recallResult, setRecallResult] = useState<Awaited<ReturnType<typeof simulateRecall>> | null>(null);
  const [recallError, setRecallError] = useState<string | null>(null);
  const [alerts, setAlerts] = useState<AlertRecord[]>([]);
  const [recallAlerts, setRecallAlerts] = useState<AlertRecord[]>([]);

  const refreshQueueDepth = useCallback(() => {
    queueDepth().then(setPendingCount);
  }, []);

  // On mount: show any scans already queued from a previous offline session.
  // On reconnect: replay them (each carrying its own Idempotency-Key so a
  // replay that the server already saw doesn't create a duplicate decision
  // or a duplicate alert), then refresh the badge count either way.
  useEffect(() => {
    refreshQueueDepth();

    const flush = async () => {
      await replayQueue(async (queuedBatchId, idempotencyKey) => {
        await scanBatch(queuedBatchId, idempotencyKey);
      });
      refreshQueueDepth();
    };

    const unsubscribe = onReconnect(flush);
    return unsubscribe;
  }, [refreshQueueDepth]);

  // FIX: the old version switched to the DECISION screen *before* the result
  // arrived and only rendered it when `decision` was set. Any failure (unknown
  // batch ID -> 404, backend down, CORS) left `decision` null, so the page went
  // blank with the error only in the console. Now we stay on the scanner and
  // show the problem, and only switch screens once we have a real decision.
  const handleScanComplete = async (scannedBatchId: string) => {
    if (isEvaluating) return;
    setScanMessage(null);
    setAlerts([]);
    setIsEvaluating(true);
    try {
      const result = await scanBatch(scannedBatchId);
      setBatchId(scannedBatchId);
      setDecision(result);
      setAppState('DECISION');
      // The backend records the alert before it responds, so it is readable now.
      getAlerts(scannedBatchId).then(setAlerts).catch(() => setAlerts([]));
    } catch (error) {
      console.error('Error evaluating batch:', error);
      const status = (error as { status?: number }).status;
      if (!navigator.onLine) {
        await enqueueScan(scannedBatchId);
        refreshQueueDepth();
        setScanMessage({
          kind: 'info',
          text: `You're offline. The scan for "${scannedBatchId}" is queued and will be sent when you reconnect.`,
        });
      } else if (status === 404) {
        setScanMessage({
          kind: 'error',
          text: `Batch "${scannedBatchId}" was not found. Check the ID and try again (demo IDs: DEMO-ACCEPT, DEMO-HOLD, DEMO-REJECT).`,
        });
      } else {
        const msg = error instanceof Error ? error.message : 'Unknown error';
        setScanMessage({
          kind: 'error',
          text: `Could not evaluate "${scannedBatchId}": ${msg}. Is the backend running on ${import.meta.env.VITE_API_BASE}?`,
        });
      }
    } finally {
      setIsEvaluating(false);
    }
  };

  // FIX: the Recall button used to just flip the screen to a hard-coded
  // banner. It now calls the real ledger recall endpoint so recipients show.
  const runRecall = async (id: string) => {
    setBatchId(id);
    setRecallResult(null);
    setRecallError(null);
    setRecallAlerts([]);
    try {
      setRecallResult(await simulateRecall(id));
      getAlerts(id).then(setRecallAlerts).catch(() => setRecallAlerts([]));
    } catch (err) {
      setRecallError(err instanceof Error ? err.message : 'Recall simulation failed');
    }
    setAppState('RECALLED');
  };

  const handleRecallTriggered = () => { if (batchId) runRecall(batchId); };

  const resetFlow = () => {
    setAppState('SCANNING');
    setBatchId(null);
    setDecision(null);
    setScanMessage(null);
    setRecallResult(null);
    setRecallError(null);
    setAlerts([]);
    setRecallAlerts([]);
  };

  // FIX: demo buttons used to call scanBatch('DEMO-BATCH-001', <status>), which
  // was written for the old mock. That batch doesn't exist in the real backend
  // and the status was being sent as the Idempotency-Key. The seeded demo batch
  // IDs are exactly DEMO-ACCEPT / DEMO-HOLD / DEMO-REJECT / DEMO-RECALL.
  const handleDemoMode = (mode: string) => {
    if (mode === 'SCANNING') { resetFlow(); return; }
    if (!mode.startsWith('DEMO-')) return;
    if (mode === 'DEMO-RECALL') { runRecall('DEMO-RECALL'); return; }
    setAppState('SCANNING');
    handleScanComplete(mode);
  };

  return (
    <div className="app-root" data-theme={isDark ? 'dark' : 'light'}>

      {/* ══ Ambient background layer ══ */}
      <div className="ambient-bg" aria-hidden="true">
        {/* Glow orbs */}
        <div className="ambient-orb ambient-orb--cyan" />
        <div className="ambient-orb ambient-orb--violet" />
        <div className="ambient-orb ambient-orb--teal" />

        {/* Repeating medical cross grid watermark */}
        <div className="watermark-grid" />

        {/* Floating DNA / helix watermark */}
        <svg className="watermark-helix" viewBox="0 0 200 600" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
          {/* Left strand */}
          <path d="M 60 0 C 120 60, 20 120, 80 180 C 140 240, 20 300, 80 360 C 140 420, 20 480, 80 540 C 140 600, 60 600, 60 600"
            fill="none" stroke="rgba(99,179,237,0.12)" strokeWidth="2.5" strokeLinecap="round"/>
          {/* Right strand */}
          <path d="M 140 0 C 80 60, 180 120, 120 180 C 60 240, 180 300, 120 360 C 60 420, 180 480, 120 540 C 60 600, 140 600, 140 600"
            fill="none" stroke="rgba(167,139,250,0.10)" strokeWidth="2.5" strokeLinecap="round"/>
          {/* Cross-rungs */}
          {[30, 90, 150, 210, 270, 330, 390, 450, 510, 570].map((y, i) => (
            <line key={i} x1="70" y1={y} x2="130" y2={y}
              stroke="rgba(99,179,237,0.09)" strokeWidth="1.5" strokeLinecap="round"/>
          ))}
          {/* Small nodes on rungs */}
          {[30, 90, 150, 210, 270, 330, 390, 450, 510, 570].map((y, i) => (
            <g key={`n${i}`}>
              <circle cx="70" cy={y} r="3" fill="rgba(34,211,238,0.15)"/>
              <circle cx="130" cy={y} r="3" fill="rgba(167,139,250,0.13)"/>
            </g>
          ))}
        </svg>

        {/* Large geometric cross accent */}
        <svg className="watermark-cross" viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
          <rect x="80" y="10" width="40" height="180" rx="6" fill="rgba(14,165,233,0.04)"/>
          <rect x="10" y="80" width="180" height="40" rx="6" fill="rgba(14,165,233,0.04)"/>
          <rect x="85" y="15" width="30" height="170" rx="4" fill="none" stroke="rgba(14,165,233,0.07)" strokeWidth="1"/>
          <rect x="15" y="85" width="170" height="30" rx="4" fill="none" stroke="rgba(14,165,233,0.07)" strokeWidth="1"/>
          <circle cx="100" cy="100" r="24" fill="none" stroke="rgba(99,179,237,0.08)" strokeWidth="1.5"/>
          <circle cx="100" cy="100" r="40" fill="none" stroke="rgba(99,179,237,0.05)" strokeWidth="1"/>
        </svg>
      </div>

      {/* ── Top Navigation ── */}

      <nav className="top-nav">
        <div className="top-nav-inner">
          <div className="nav-brand">
            <span className="nav-logo">✚</span>
            <span className="nav-title">MediTrust</span>
            <span className="nav-badge">Quality Gate</span>
          </div>

          {pendingCount > 0 && (
            <div
              className="offline-queue-badge"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.4rem',
                padding: '0.35rem 0.75rem',
                borderRadius: '999px',
                background: '#fef3c7',
                color: '#92400e',
                fontSize: '0.85rem',
                fontWeight: 600,
              }}
            >
              <WifiOff size={14} strokeWidth={2.2} />
              <span>{pendingCount} scan{pendingCount > 1 ? 's' : ''} queued</span>
            </div>
          )}

          {batchId && appState !== 'SCANNING' && (
            <button className="btn-new-scan" onClick={resetFlow}>
              + New Scan
            </button>
          )}

          {/* Dark mode toggle */}
          <button
            className="theme-toggle"
            onClick={() => setIsDark(d => !d)}
            title={isDark ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
            aria-label="Toggle dark mode"
          >
            {isDark
              ? <Sun size={16} strokeWidth={2} />
              : <Moon size={16} strokeWidth={2} />}
            <span>{isDark ? 'Light' : 'Dark'}</span>
          </button>
        </div>
      </nav>

      {/* ── KPI Sub-header ── */}
      <div className="kpi-section">
        <div className="page-inner">
          <KpiBar />
        </div>
      </div>

      {/* ── Main content ── */}
      <main className="page-inner animate-fade-in" style={{ paddingTop: '2rem', paddingBottom: '4rem' }}>

        {appState === 'SCANNING' && (
          <>
            {isEvaluating && (
              <div style={{ maxWidth: '620px', margin: '0 auto 1rem', padding: '0.75rem 1rem', borderRadius: '8px', background: '#e0f2fe', color: '#075985', fontWeight: 600 }}>
                Evaluating batch…
              </div>
            )}
            {scanMessage && (
              <div
                role="alert"
                style={{
                  maxWidth: '620px', margin: '0 auto 1rem', padding: '0.75rem 1rem', borderRadius: '8px', fontWeight: 600,
                  background: scanMessage.kind === 'error' ? '#fee2e2' : '#fef3c7',
                  color: scanMessage.kind === 'error' ? '#991b1b' : '#92400e',
                }}
              >
                {scanMessage.text}
              </div>
            )}
            <ScanIntake onScanComplete={handleScanComplete} />
          </>
        )}

        {appState === 'DECISION' && decision && (
          <div className="flex-row gap-6" style={{ alignItems: 'flex-start' }}>
            <div className="flex-col gap-6" style={{ flex: 2 }}>
              {alerts.length > 0 && (
                <div className="card" role="status" style={{ borderLeft: '4px solid #ef4444' }}>
                  <strong>Alert sent to {alerts[0].recipient}</strong>
                  <span style={{ marginLeft: '0.5rem', fontSize: '0.8rem', opacity: 0.75 }}>
                    {alerts[0].channel} · {alerts[0].status} · {alerts[0].language}
                  </span>
                  <p lang={alerts[0].language.toLowerCase()} style={{ margin: '0.5rem 0 0', fontSize: '1.05rem' }}>
                    {alerts[0].message}
                  </p>
                </div>
              )}
              <DecisionScreen decision={decision} />
              <AuditTrail batchId={batchId!} />
              <RecallFlow batchId={batchId!} onRecallTriggered={handleRecallTriggered} />
            </div>
            <div style={{ flex: 1, position: 'sticky', top: '5.5rem' }}>
              <ChatPanel batchData={decision} />
            </div>
          </div>
        )}

        {appState === 'RECALLED' && (
          <div className="card recall-banner animate-fade-in">
            <div className="recall-header-row">
              <div className="recall-icon-pulse">
                <AlertOctagon size={32} strokeWidth={2.2} />
              </div>
              <div>
                <div className="recall-sub-badge">CRITICAL SAFETY GATE INTERVENTION</div>
                <h2 className="recall-title">RECALL INITIATED</h2>
              </div>
            </div>

            <p className="recall-description">
              The quality-gate system has successfully quarantined and recalled Batch <strong className="recall-batch-id">{batchId || 'DEMO-BATCH-001'}</strong>.
              All downstream distribution nodes, hospital pharmacy dispense hubs, and automated locks have received instant halt orders.
            </p>

            {/* Structured Info Grid */}
            <div className="recall-info-grid">
              <div className="recall-info-card">
                <div className="recall-info-icon-wrap">
                  <Tag size={15} />
                </div>
                <div className="recall-info-content">
                  <span className="recall-info-label">BATCH ID</span>
                  <span className="recall-info-value recall-mono-tag">{batchId || 'DEMO-BATCH-001'}</span>
                </div>
              </div>

              <div className="recall-info-card">
                <div className="recall-info-icon-wrap">
                  <Clock size={15} />
                </div>
                <div className="recall-info-content">
                  <span className="recall-info-label">ALERT TIMESTAMP</span>
                  <span className="recall-info-value">{recallResult?.triggered_at ? new Date(String(recallResult.triggered_at)).toLocaleString() : '—'}</span>
                </div>
              </div>

              <div className="recall-info-card">
                <div className="recall-info-icon-wrap">
                  <Boxes size={15} />
                </div>
                <div className="recall-info-content">
                  <span className="recall-info-label">AFFECTED UNITS</span>
                  <span className="recall-info-value">{recallResult ? `${recallResult.affected_departments.length} locations` : '—'}</span>
                </div>
              </div>

              <div className="recall-info-card">
                <div className="recall-info-icon-wrap">
                  <ThermometerSnowflake size={15} />
                </div>
                <div className="recall-info-content">
                  <span className="recall-info-label">PRIMARY REASON</span>
                  <span className="recall-info-value recall-reason-tag">{recallResult ? `${recallResult.recipients.length} recipients notified` : 'Recall not confirmed'}</span>
                </div>
              </div>
            </div>

            {recallError && (
              <p role="alert" style={{ color: '#991b1b', fontWeight: 600, margin: '1rem 0' }}>
                Recall could not be simulated: {recallError}
              </p>
            )}
            {recallResult && recallResult.recipients.length > 0 && (
              <div style={{ margin: '1rem 0' }}>
                <strong>Recall notice sent to:</strong>
                <ul style={{ margin: '0.5rem 0 0 1.25rem' }}>
                  {recallResult.recipients.map((r) => <li key={r}>{r}</li>)}
                </ul>
              </div>
            )}

            {recallAlerts.length > 0 && (
              <p style={{ margin: '1rem 0' }}>
                <strong>{recallAlerts.length} alerts queued for SMS:</strong>{' '}
                <span lang={recallAlerts[0].language.toLowerCase()}>{recallAlerts[0].message}</span>
              </p>
            )}

            {/* Actions */}
            <div className="recall-actions-row">
              <button className="recall-acknowledge-btn" onClick={resetFlow}>
                <RotateCcw size={16} />
                <span>Acknowledge &amp; Return to Scanner</span>
              </button>
            </div>
          </div>
        )}

        {/* ── Live Activity Marquee Ticker ── */}
        <div style={{ marginTop: '2.5rem' }}>
          <ActivityTicker />
        </div>
      </main>

      <DemoToggle onForceState={handleDemoMode} />
    </div>
  );
}

export default App;