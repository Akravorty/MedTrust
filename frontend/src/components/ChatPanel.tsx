import { useState, useRef, useEffect } from 'react';
import { askAgent } from '../services/api';
import { Send, Bot, FileText } from 'lucide-react';
import { QAResponse } from '../types/schema';

interface Message {
  id: string;
  role: 'user' | 'agent';
  text: string;
  responseMeta?: QAResponse;
}

/** Minimal markdown → JSX: bold (**text**), bullet lines (• or *) */
function MarkdownText({ text }: { text: string }) {
  return (
    <span style={{ whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>
      {text.split('\n').map((line, li) => {
        // Bold spans: **text**
        const parts = line.split(/(\*\*[^*]+\*\*)/g).map((part, pi) =>
          part.startsWith('**') && part.endsWith('**')
            ? <strong key={pi}>{part.slice(2, -2)}</strong>
            : <span key={pi}>{part}</span>
        );
        // Indent bullet lines
        const isBullet = line.startsWith('•') || /^\* /.test(line);
        return (
          <span key={li} style={{ display: 'block', paddingLeft: isBullet ? '0.25rem' : 0 }}>
            {parts}
          </span>
        );
      })}
    </span>
  );
}

function mkId() { return `${Date.now()}-${Math.random().toString(36).slice(2)}`; }

export default function ChatPanel({ batchData }: { batchData: import('../types/schema').BatchDecision }) {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: mkId(),
      role: 'agent',
      text: `I am the MediTrust QA Agent. How can I help you evaluate batch ${batchData.batch_id}?`,
    },
  ]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to latest message
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  const handleSend = async () => {
    // ── Guard: no empty input, no double-send while loading ──
    if (!input.trim() || loading) return;

    const q = input.trim();
    setInput('');           // clear immediately on submit

    const userMsg: Message = { id: mkId(), role: 'user', text: q };
    setMessages(prev => [...prev, userMsg]);

    setLoading(true);
    try {
      // Step 3: Pass only batchData and q since backend endpoint is stateless
      const res = await askAgent(batchData, q);
      setMessages(prev => [
        ...prev,
        { id: mkId(), role: 'agent', text: res.answer, responseMeta: res },
      ]);
    } catch {
      setMessages(prev => [
        ...prev,
        { id: mkId(), role: 'agent', text: 'Error connecting to QA service. Please retry.' },
      ]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="card chat-panel-card flex-col" style={{ height: '580px', padding: 0, overflow: 'hidden' }}>

      {/* Header */}
      <div className="chat-header">
        <h3 className="flex-row items-center gap-2" style={{ fontSize: '0.95rem', fontWeight: 700, margin: 0 }}>
          <Bot size={18} className="chat-header-bot-icon" /> Ask MediTrust Agent
        </h3>
      </div>

      {/* Messages viewport */}
      <div className="flex-col gap-4" style={{ flex: 1, overflowY: 'auto', padding: '1rem' }}>
        {messages.map(msg => (
          <div key={msg.id} className={`flex-col ${msg.role === 'user' ? 'items-end' : 'items-start'}`}>
            <div className={`chat-bubble ${msg.role === 'user' ? 'chat-bubble--user' : 'chat-bubble--agent'}`}>
              <MarkdownText text={msg.text} />
            </div>

            {msg.responseMeta && (
              <div className="flex-row items-center gap-2" style={{ marginTop: '0.4rem', fontSize: '0.75rem', color: '#64748b', flexWrap: 'wrap' }}>
                <span className="chat-meta-conf">
                  {msg.responseMeta.confidence} Conf
                </span>
                {msg.responseMeta.evidence_sources.map(src => (
                  <span key={src} className="flex-row items-center gap-1" title={src}>
                    <FileText size={11} /> {src}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}

        {loading && (
          <div className="flex-row items-center gap-2 animate-pulse" style={{ color: '#64748b', fontSize: '0.82rem', padding: '0.25rem 0' }}>
            <span style={{ display: 'flex', gap: 3 }}>
              {[0, 1, 2].map(i => (
                <span key={i} style={{
                  width: 6, height: 6, borderRadius: '50%', background: '#94a3b8',
                  animation: `pulse 1.2s ease-in-out ${i * 0.2}s infinite`,
                }} />
              ))}
            </span>
            Agent is analyzing ledger…
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input row */}
      <div className="chat-input-row">
        <div className="flex-row gap-2">
          <input
            type="text"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !loading) handleSend(); }}
            placeholder="Ask about batch risk parameters..."
            className="chat-input-field"
            disabled={loading}
          />
          <button
            className="btn-primary flex-row items-center justify-center"
            style={{ width: '42px', height: '42px', padding: 0, flexShrink: 0 }}
            onClick={handleSend}
            disabled={loading || !input.trim()}
          >
            <Send size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}