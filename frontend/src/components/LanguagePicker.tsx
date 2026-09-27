import { useState, useRef, useEffect } from 'react';
import { Languages } from 'lucide-react';
import { LANGUAGES, useI18n } from '../i18n';

/** Small dropdown, meant for the top bar. Shows the native script for each
 * language so someone can find their own language without reading English. */
export default function LanguagePicker() {
  const { lang, info, setLang } = useI18n();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen(o => !o)}
        className="webcam-badge"
        title="Change language"
        style={{ position: 'static' }}
      >
        <Languages size={13} />
        {info.native}
      </button>
      {open && (
        <div
          role="listbox"
          style={{
            position: 'absolute', right: 0, top: 'calc(100% + 6px)', zIndex: 20,
            background: 'var(--card-bg, #fff)', border: '1px solid rgba(0,0,0,0.12)',
            borderRadius: '10px', boxShadow: '0 8px 24px rgba(0,0,0,0.18)',
            maxHeight: '320px', overflowY: 'auto', minWidth: '200px', padding: '4px',
          }}
        >
          {LANGUAGES.map(l => (
            <button
              key={l.code}
              role="option"
              aria-selected={l.code === lang}
              onClick={() => { setLang(l.code); setOpen(false); }}
              style={{
                display: 'flex', justifyContent: 'space-between', width: '100%',
                padding: '8px 10px', borderRadius: '6px', border: 'none',
                background: l.code === lang ? 'rgba(14,165,233,0.12)' : 'transparent',
                cursor: 'pointer', textAlign: 'left', fontSize: '0.9rem',
              }}
            >
              <span>{l.native}</span>
              <span style={{ opacity: 0.55, fontSize: '0.78rem' }}>{l.name}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
