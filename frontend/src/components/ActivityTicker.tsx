import React from 'react';
import { Activity } from 'lucide-react';

interface TickerEvent {
  id: string;
  batchId: string;
  action: string;
  time: string;
  type: 'accept' | 'hold' | 'reject';
}

const QUALITY_EVENTS: TickerEvent[] = [
  { id: '1', batchId: 'BATCH-702', action: 'Spectrometry Verified', time: '2m ago', type: 'accept' },
  { id: '2', batchId: 'BATCH-881', action: 'Cold-Chain Temp Alert', time: '5m ago', type: 'hold' },
  { id: '3', batchId: 'BATCH-901', action: 'Barcode & Seal Cleared', time: '12m ago', type: 'accept' },
  { id: '4', batchId: 'BATCH-619', action: 'Manual Review Initiated', time: '18m ago', type: 'hold' },
  { id: '5', batchId: 'BATCH-410', action: 'Packaging Defect Flagged', time: '26m ago', type: 'reject' },
  { id: '6', batchId: 'BATCH-334', action: 'Chromatography Pass 99.4%', time: '34m ago', type: 'accept' },
  { id: '7', batchId: 'BATCH-289', action: 'Released to ICU Ward B', time: '41m ago', type: 'accept' },
  { id: '8', batchId: 'BATCH-190', action: 'Viscosity Out of Bounds', time: '53m ago', type: 'hold' },
];

export default function ActivityTicker() {
  // Duplicate array to achieve seamless infinite loop
  const displayItems = [...QUALITY_EVENTS, ...QUALITY_EVENTS];

  const getDotClass = (type: TickerEvent['type']) => {
    switch (type) {
      case 'accept': return 'ticker-dot--accept';
      case 'hold': return 'ticker-dot--hold';
      case 'reject': return 'ticker-dot--reject';
    }
  };

  const getSymbol = (type: TickerEvent['type']) => {
    switch (type) {
      case 'accept': return '✓';
      case 'hold': return '⚠';
      case 'reject': return '✕';
    }
  };

  return (
    <div className="activity-ticker-container">
      {/* Static leading label */}
      <div className="ticker-label">
        <span className="ticker-live-dot" />
        <Activity size={13} className="ticker-label-icon" />
        <span>LIVE AUDIT STREAM</span>
      </div>

      {/* Marquee viewport */}
      <div className="ticker-viewport">
        <div className="ticker-track">
          {displayItems.map((event, index) => (
            <div key={`${event.id}-${index}`} className="ticker-item">
              <span className={`ticker-dot ${getDotClass(event.type)}`}>
                {getSymbol(event.type)}
              </span>
              <span className="ticker-batch">{event.batchId}</span>
              <span className="ticker-action">{event.action}</span>
              <span className="ticker-time">· {event.time}</span>
              <span className="ticker-separator">|</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
