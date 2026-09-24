// frontend/src/components/AlertAudioButton.tsx
//
// Small play/pause button for a spoken alert clip. Checks whether a
// recording actually exists for this language+status before rendering
// anything -- currently only en/hi/or/mr x HOLD/REJECT/RECALL have audio
// (see data/generate_alert_audio.py), so most language selections will
// correctly render nothing here rather than a broken player.

import { useEffect, useRef, useState } from 'react';
import { Volume2, Loader2 } from 'lucide-react';
import { checkAlertAudioExists, getAlertAudioUrl } from '../services/api';

interface Props {
  lang: string;     // lowercase code, e.g. 'hi', 'mr', 'or', 'en'
  status: string;    // 'HOLD' | 'REJECT' | 'RECALL'
  label?: string;    // optional text next to the icon
}

export default function AlertAudioButton({ lang, status, label }: Props) {
  const [available, setAvailable] = useState<boolean | null>(null); // null = still checking
  const [playing, setPlaying] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    setAvailable(null);
    checkAlertAudioExists(lang, status).then((exists) => {
      if (!cancelled) setAvailable(exists);
    });
    return () => {
      cancelled = true;
    };
  }, [lang, status]);

  useEffect(() => {
    // Stop playback if the language/status changes out from under us
    // (e.g. the demo switches language picker mid-flow).
    return () => {
      audioRef.current?.pause();
    };
  }, [lang, status]);

  if (available === false) return null; // no clip recorded — render nothing, not a broken control

  const handleClick = () => {
    if (!audioRef.current) return;
    if (playing) {
      audioRef.current.pause();
    } else {
      audioRef.current.currentTime = 0;
      audioRef.current.play().catch(() => setPlaying(false));
    }
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={available === null}
      title={available === null ? 'Checking for audio…' : playing ? 'Pause' : 'Play spoken alert'}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '0.35rem',
        marginLeft: '0.6rem',
        padding: '0.2rem 0.55rem',
        borderRadius: '999px',
        border: '1px solid rgba(0,0,0,0.15)',
        background: playing ? 'rgba(59,130,246,0.12)' : 'transparent',
        cursor: available === null ? 'default' : 'pointer',
        fontSize: '0.78rem',
        opacity: available === null ? 0.6 : 1,
      }}
    >
      {available === null ? <Loader2 size={13} className="animate-spin" /> : <Volume2 size={13} />}
      {label ?? (playing ? 'Playing…' : 'Listen')}
      <audio
        ref={audioRef}
        src={available ? getAlertAudioUrl(lang, status) : undefined}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        preload="none"
      />
    </button>
  );
}
