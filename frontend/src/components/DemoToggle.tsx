import { useState } from 'react';
import {
  Settings, ChevronDown, ChevronUp,
  CheckCircle2, AlertTriangle, XCircle, Biohazard, RotateCcw
} from 'lucide-react';

interface Props {
  onForceState: (mode: string) => void;
}

type DemoMode = 'DEMO-ACCEPT' | 'DEMO-HOLD' | 'DEMO-REJECT' | 'DEMO-RECALL' | null;

const MODES = [
  {
    id: 'DEMO-ACCEPT' as DemoMode,
    label: 'Accept',
    Icon: CheckCircle2,
    color: '#10b981',
    bg: '#d1fae5',
    border: '#6ee7b7',
    glow: 'rgba(16,185,129,0.25)',
  },
  {
    id: 'DEMO-HOLD' as DemoMode,
    label: 'Hold',
    Icon: AlertTriangle,
    color: '#d97706',
    bg: '#fef3c7',
    border: '#fcd34d',
    glow: 'rgba(245,158,11,0.25)',
  },
  {
    id: 'DEMO-REJECT' as DemoMode,
    label: 'Reject',
    Icon: XCircle,
    color: '#ef4444',
    bg: '#fee2e2',
    border: '#fca5a5',
    glow: 'rgba(239,68,68,0.25)',
  },
  {
    id: 'DEMO-RECALL' as DemoMode,
    label: 'Recall',
    Icon: Biohazard,
    color: '#a21caf',
    bg: '#fae8ff',
    border: '#e879f9',
    glow: 'rgba(162,28,175,0.25)',
  },
];

export default function DemoToggle({ onForceState }: Props) {
  const [collapsed, setCollapsed] = useState(false);
  const [active, setActive] = useState<DemoMode>(null);

  const handleSelect = (mode: DemoMode) => {
    setActive(mode);
    onForceState(mode!);
  };

  const handleReset = () => {
    setActive(null);
    onForceState('SCANNING');
  };

  /* ── Collapsed pill ── */
  if (collapsed) {
    return (
      <button
        className="demo-pill"
        onClick={() => setCollapsed(false)}
        title="Expand Demo Mode"
      >
        <Settings size={14} className="demo-pill-icon" />
        <span>DEMO</span>
        {active && (
          <span
            className="demo-pill-dot"
            style={{ backgroundColor: MODES.find(m => m.id === active)?.color ?? '#64748b' }}
          />
        )}
      </button>
    );
  }

  /* ── Expanded panel ── */
  return (
    <div className="demo-panel">

      {/* Header */}
      <div className="demo-header">
        <div className="demo-header-left">
          <Settings size={13} style={{ color: '#94a3b8' }} />
          <span className="demo-title">DEMO MODE</span>
          {active && (
            <span
              className="demo-active-badge animate-fade-in"
              style={{
                backgroundColor: MODES.find(m => m.id === active)?.bg,
                color: MODES.find(m => m.id === active)?.color,
                borderColor: MODES.find(m => m.id === active)?.border,
              }}
            >
              {MODES.find(m => m.id === active)?.label} active
            </span>
          )}
        </div>
        <button
          className="demo-collapse-btn"
          onClick={() => setCollapsed(true)}
          title="Minimise"
        >
          <ChevronDown size={14} />
        </button>
      </div>

      {/* Mode buttons */}
      <div className="demo-modes">
        {MODES.map(({ id, label, Icon, color, bg, border, glow }) => {
          const isActive = active === id;
          return (
            <button
              key={id}
              className={`demo-mode-btn ${isActive ? 'demo-mode-btn--active' : ''}`}
              style={isActive
                ? { backgroundColor: bg, borderColor: border, boxShadow: `0 0 0 3px ${glow}`, color }
                : {}}
              onClick={() => handleSelect(id)}
            >
              <Icon size={14} style={{ color: isActive ? color : undefined, flexShrink: 0 }} />
              <span>{label}</span>
              {isActive && <span className="demo-mode-check" style={{ color }}>✓</span>}
            </button>
          );
        })}
      </div>

      {/* Reset */}
      <button className="demo-reset-btn" onClick={handleReset}>
        <RotateCcw size={12} />
        Reset to Scan Screen
      </button>
    </div>
  );
}
