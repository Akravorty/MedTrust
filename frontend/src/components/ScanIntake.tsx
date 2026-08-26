import { useState, useRef, useEffect, useCallback } from 'react';
import { Camera, Wifi, CheckCircle2 } from 'lucide-react';
import { BrowserMultiFormatReader, Result } from '@zxing/library';

interface Props {
  onScanComplete: (batchId: string) => void;
}

/* ─── tiny SVG helpers ─── */
function Spinner() {
  return (
    <svg className="btn-spinner" viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" stroke="rgba(255,255,255,0.3)" strokeWidth="2.5" />
      <path d="M12 2a10 10 0 0 1 10 10" stroke="white" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  );
}
function CheckIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}
function SearchIcon() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  );
}

/* ─── success beep via Web Audio API ─── */
function playBeep() {
  try {
    const ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(988, ctx.currentTime);
    gain.gain.setValueAtTime(0.18, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.22);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.22);
  } catch { /* AudioContext blocked */ }
}

export default function ScanIntake({ onScanComplete }: Props) {
  /* ── state ── */
  const [scanning, setScanning]             = useState(false);
  const [done, setDone]                     = useState(false);
  const [batchInput, setBatchInput]         = useState('');
  const [isWebcamActive, setIsWebcamActive] = useState(false);
  const [inputFocused, setInputFocused]     = useState(false);
  const [flashEffect, setFlashEffect]       = useState(false);
  const [detectedCode, setDetectedCode]     = useState<string | null>(null);

  /* ── refs ── */
  const inputRef        = useRef<HTMLInputElement>(null);
  const videoRef        = useRef<HTMLVideoElement | null>(null);
  const streamRef       = useRef<MediaStream | null>(null);
  const codeReaderRef   = useRef<BrowserMultiFormatReader | null>(null);
  const animFrameRef    = useRef<number | null>(null);
  const isProcessingRef = useRef(false);

  /* ─── submit ─── */
  const handleScanSubmit = useCallback((idToScan?: string) => {
    const targetId = (idToScan ?? batchInput).trim();
    if (!targetId || isProcessingRef.current) return;
    isProcessingRef.current = true;
    setScanning(true);
    setTimeout(() => {
      setScanning(false);
      setDone(true);
      setTimeout(() => {
        setDone(false);
        isProcessingRef.current = false;
        onScanComplete(targetId);
      }, 600);
    }, 1800);
  }, [batchInput, onScanComplete]);

  /* ─── stop all engines ─── */
  const stopWebcam = useCallback(() => {
    if (animFrameRef.current !== null) {
      cancelAnimationFrame(animFrameRef.current);
      animFrameRef.current = null;
    }
    if (codeReaderRef.current) {
      try { codeReaderRef.current.reset(); } catch { /* ignore */ }
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop());
      streamRef.current = null;
    }
    setIsWebcamActive(false);
    setDetectedCode(null);
  }, []);

  /* ─── barcode detected ─── */
  const onBarcodeDetected = useCallback((raw: string) => {
    if (isProcessingRef.current || !raw) return;
    const clean = raw.startsWith('http') ? (raw.split('/').pop() ?? raw) : raw;
    playBeep();
    setFlashEffect(true);
    setTimeout(() => setFlashEffect(false), 500);
    setDetectedCode(clean);
    setBatchInput(clean);
    stopWebcam();
    handleScanSubmit(clean);
  }, [handleScanSubmit, stopWebcam]);

  /* ─── start webcam ─── */
  const startWebcam = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'environment', width: { ideal: 1280 }, height: { ideal: 720 } },
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
      setIsWebcamActive(true);

      /* Strategy 1 – native BarcodeDetector (Chrome/Edge, hardware-accelerated) */
      if ('BarcodeDetector' in window) {
        try {
          const BD = (window as any).BarcodeDetector;
          const formats: string[] = (await BD.getSupportedFormats?.()) ??
            ['qr_code', 'code_128', 'code_39', 'ean_13', 'upc_a', 'data_matrix'];
          const detector = new BD({ formats });
          const loop = async () => {
            if (!videoRef.current || videoRef.current.readyState < 2 || isProcessingRef.current) {
              animFrameRef.current = requestAnimationFrame(loop);
              return;
            }
            try {
              const codes: Array<{ rawValue: string }> = await detector.detect(videoRef.current);
              if (codes.length > 0) { onBarcodeDetected(codes[0].rawValue); return; }
            } catch { /* ignore per-frame errors */ }
            animFrameRef.current = requestAnimationFrame(loop);
          };
          animFrameRef.current = requestAnimationFrame(loop);
          return;
        } catch (e) {
          console.warn('[Scanner] BarcodeDetector failed, using ZXing', e);
        }
      }

      /* Strategy 2 – ZXing BrowserMultiFormatReader */
      if (!codeReaderRef.current) codeReaderRef.current = new BrowserMultiFormatReader();
      if (videoRef.current) {
        codeReaderRef.current.decodeFromVideoDevice(
          null,
          videoRef.current,
          (result: Result | null) => {
            if (result && !isProcessingRef.current) onBarcodeDetected(result.getText());
          },
        );
      }
    } catch (err) {
      console.error('[Scanner] Camera init error:', err);
      alert('Unable to access webcam. Please check browser permissions.');
      setIsWebcamActive(false);
    }
  };

  /* ─── cleanup on unmount ─── */
  useEffect(() => () => { stopWebcam(); }, [stopWebcam]);

  const isEmpty = !batchInput.trim();

  return (
    <div className="card scan-card" style={{ maxWidth: '620px', margin: '2.5rem auto' }}>

      {/* ── Title ── */}
      <div style={{ textAlign: 'center', marginBottom: '1.5rem' }}>
        <h2 className="scan-title">Scan Medicine Batch</h2>
        <p className="scan-subtitle">
          Align the barcode or QR code within the frame, or enter the ID manually below.
        </p>
      </div>

      {/* ── Viewport ── */}
      <div
        className={`scanner-viewport ${flashEffect ? 'scanner-viewport--flash' : ''}`}
        onClick={() => inputRef.current?.focus()}
      >
        {/* Live video */}
        <video
          ref={videoRef}
          autoPlay
          playsInline
          muted
          style={{
            display: isWebcamActive ? 'block' : 'none',
            position: 'absolute',
            inset: 0,
            width: '100%',
            height: '100%',
            objectFit: 'cover',
            borderRadius: '8px',
            zIndex: 1,
          }}
        />

        {/* Corner reticles */}
        <span className={`reticle reticle-tl ${detectedCode ? 'reticle--acquired' : ''}`} style={{ zIndex: 3 }} />
        <span className={`reticle reticle-tr ${detectedCode ? 'reticle--acquired' : ''}`} style={{ zIndex: 3 }} />
        <span className={`reticle reticle-bl ${detectedCode ? 'reticle--acquired' : ''}`} style={{ zIndex: 3 }} />
        <span className={`reticle reticle-br ${detectedCode ? 'reticle--acquired' : ''}`} style={{ zIndex: 3 }} />
        <div className={`scan-line ${scanning ? 'scan-line--active' : ''}`} style={{ zIndex: 3 }} />

        {/* Target box overlay */}
        {isWebcamActive && (
          <div className={`target-box-overlay ${detectedCode ? 'target-box-overlay--acquired' : ''}`} style={{ zIndex: 3 }} />
        )}

        {/* Detected badge */}
        {detectedCode && (
          <div className="detected-qr-badge animate-fade-in" style={{ zIndex: 4 }}>
            <CheckCircle2 size={13} color="#10b981" />
            <span>QR DETECTED: <strong>{detectedCode}</strong></span>
          </div>
        )}

        {/* Static placeholder */}
        {!isWebcamActive && (
          <div className="scanner-icon-wrap" style={{ zIndex: 2 }}>
            <Camera size={52} strokeWidth={1.4} className="scanner-camera-icon" />
            {scanning && <p className="scanning-label animate-pulse">Reading batch data…</p>}
          </div>
        )}

        {/* Scanning overlay while webcam is running */}
        {isWebcamActive && scanning && !detectedCode && (
          <div className="scanner-icon-wrap" style={{ zIndex: 4, position: 'absolute' }}>
            <p className="scanning-label animate-pulse"
              style={{ background: 'rgba(0,0,0,0.65)', padding: '4px 14px', borderRadius: '12px', color: '#38bdf8' }}>
              Reading batch data…
            </p>
          </div>
        )}

        {/* Camera toggle */}
        <button
          className="webcam-badge"
          style={{ zIndex: 5 }}
          onClick={e => { e.stopPropagation(); isWebcamActive ? stopWebcam() : startWebcam(); }}
          title={isWebcamActive ? 'Stop Camera' : 'Switch to Webcam'}
        >
          <span className="status-dot" style={{ backgroundColor: isWebcamActive ? '#ef4444' : '#22c55e' }} />
          <Wifi size={12} />
          {isWebcamActive ? 'Stop Camera' : 'Switch to Webcam'}
        </button>
      </div>

      {/* ── Input area ── */}
      <div style={{ marginTop: '1.5rem' }}>
        <div className={`scan-input-wrap ${inputFocused ? 'scan-input-wrap--focused' : ''} ${scanning ? 'scan-input-wrap--scanning' : ''}`}>
          <span className="scan-input-prefix">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
              stroke={inputFocused ? '#0ea5e9' : '#94a3b8'}
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
              style={{ transition: 'stroke 0.2s' }}>
              <rect x="3" y="3" width="18" height="18" rx="2"/>
              <rect x="7" y="7" width="3" height="3"/>
              <rect x="14" y="7" width="3" height="3"/>
              <rect x="14" y="14" width="3" height="3"/>
              <rect x="7" y="14" width="3" height="3"/>
            </svg>
          </span>

          <input
            ref={inputRef}
            type="text"
            placeholder="Batch ID — e.g. BATCH-892 or scan QR"
            value={batchInput}
            onChange={e => setBatchInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') handleScanSubmit(); }}
            onFocus={() => setInputFocused(true)}
            onBlur={() => setInputFocused(false)}
            className="scan-input-field"
            disabled={scanning}
            autoComplete="off"
            spellCheck={false}
          />

          {inputFocused && !isEmpty && !scanning && (
            <span className="enter-hint"><kbd>↵</kbd> Enter</span>
          )}

          {batchInput && !scanning && (
            <button
              className="scan-clear-btn"
              onClick={() => { setBatchInput(''); setDetectedCode(null); inputRef.current?.focus(); }}
              tabIndex={-1}
              title="Clear"
            >×</button>
          )}
        </div>

        <button
          className={`scan-submit-btn ${scanning ? 'scan-submit-btn--loading' : ''} ${done ? 'scan-submit-btn--done' : ''}`}
          onClick={() => handleScanSubmit()}
          disabled={isEmpty || scanning}
        >
          {scanning ? (
            <><Spinner /><span>Extracting &amp; Verifying…</span></>
          ) : done ? (
            <><CheckIcon /><span>Verified!</span></>
          ) : (
            <><SearchIcon /><span>Extract &amp; Verify</span></>
          )}
        </button>

        <p className="shortcut-hint">
          Press <kbd className="kbd">Enter ↵</kbd> to submit · <kbd className="kbd">Esc</kbd> to clear
        </p>
      </div>
    </div>
  );
}