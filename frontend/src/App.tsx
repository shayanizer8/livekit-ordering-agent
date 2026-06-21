import { useCallback, useEffect, useRef, useState } from 'react';
import microphoneSvg from './microphone-svgrepo-com.svg';
import { RoomAudioRenderer } from '@livekit/components-react';
import {
  Room,
  RoomEvent,
  RemoteParticipant,
  Track,
  type RemoteTrack,
  type TranscriptionSegment,
} from 'livekit-client';

// ─── Config ──────────────────────────────────────────────────────────────────

const TOKEN_SERVER_URL =
  import.meta.env.VITE_TOKEN_SERVER_URL ?? 'http://localhost:8080';

// ─── Constants ───────────────────────────────────────────────────────────────

/** Base waveform bar heights (px) — same values as the original static UI. */
const BASE_HEIGHTS = [
  26, 48, 34, 19, 42, 24, 37, 28, 31, 17, 45, 23, 39, 22, 29, 34, 18, 46, 25,
  32, 21, 40, 27, 30,
];

// ─── Types ───────────────────────────────────────────────────────────────────

interface CartItem {
  name: string;
  quantity: number;
  modifiers: string;
  price: string;
}

interface CartState {
  items: CartItem[];
  total: string;
  confirmed: boolean;
}

interface TokenResponse {
  token: string;
  url: string;
}

type AgentStatus = 'Listening' | 'Thinking' | 'Speaking';

// ─── Helpers ─────────────────────────────────────────────────────────────────

async function fetchToken(roomName: string): Promise<TokenResponse> {
  const url = `${TOKEN_SERVER_URL}/token?room=${encodeURIComponent(roomName)}&identity=user-${Date.now()}`;
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`Token server error — HTTP ${res.status}`);
  }
  return res.json() as Promise<TokenResponse>;
}

// ─── Component ───────────────────────────────────────────────────────────────

function App() {
  const roomRef = useRef<Room | null>(null);
  const rafRef = useRef<number>(0);

  // Live state
  const [room, setRoom] = useState<Room | null>(null);
  const [micEnabled, setMicEnabled] = useState(false);
  const [audioLevel, setAudioLevel] = useState(0);
  const [transcript, setTranscript] = useState('');
  const [status, setStatus] = useState<AgentStatus>('Listening');
  const [cart, setCart] = useState<CartState>({
    items: [],
    total: '$0.00',
    confirmed: false,
  });
  const [confirmPending, setConfirmPending] = useState(false);
  const [orderId, setOrderId] = useState('');
  const [connError, setConnError] = useState<string | null>(null);
  const [sessionEnded, setSessionEnded] = useState(false);
  const [sessionKey, setSessionKey] = useState(0); // incremented to force reconnect

  // ── Audio-level polling via rAF ────────────────────────────────────────────

  const startPolling = useCallback(() => {
    const poll = () => {
      const level = roomRef.current?.localParticipant?.audioLevel ?? 0;
      setAudioLevel(level);
      rafRef.current = requestAnimationFrame(poll);
    };
    rafRef.current = requestAnimationFrame(poll);
  }, []);

  const stopPolling = useCallback(() => {
    cancelAnimationFrame(rafRef.current);
  }, []);

  // ── Room connection ────────────────────────────────────────────────────────

  useEffect(() => {
    let cancelled = false;

    const connect = async () => {
      try {
        // Fresh room per session so LiveKit dispatches the agent worker.
        const roomName = `ordering-room-${Date.now()}`;
        const { token, url } = await fetchToken(roomName);
        if (cancelled) return;

        const livekitRoom = new Room();
        roomRef.current = livekitRoom;

        // ── Remote audio — attach before connect so we don't miss tracks ───
        livekitRoom.on(
          RoomEvent.TrackSubscribed,
          (track: RemoteTrack, _pub, participant) => {
            if (
              track.kind === Track.Kind.Audio &&
              participant instanceof RemoteParticipant
            ) {
              track.attach();
            }
          },
        );

        // ── Data channel messages ──────────────────────────────────────────
        livekitRoom.on(
          RoomEvent.DataReceived,
          (payload: Uint8Array) => {
            try {
              const msg = JSON.parse(new TextDecoder().decode(payload)) as Record<
                string,
                unknown
              >;

              if (msg.type === 'cart_update') {
                setCart({
                  items: (msg.items as CartItem[]) ?? [],
                  total: (msg.total as string) ?? '$0.00',
                  confirmed: Boolean(msg.confirmed),
                });
                if (msg.confirmed) {
                  setConfirmPending(false);
                }
              }

              // Optional: agent explicitly broadcasts its state
              if (msg.type === 'agent_state') {
                const s = (msg.state as string | undefined)?.toLowerCase();
                if (s === 'thinking') setStatus('Thinking');
                else if (s === 'speaking') setStatus('Speaking');
                else if (s === 'listening') setStatus('Listening');
              }
            } catch {
              // Ignore non-JSON or malformed messages
            }
          },
        );

        // ── Live transcription ─────────────────────────────────────────────
        livekitRoom.on(
          RoomEvent.TranscriptionReceived,
          (segments: TranscriptionSegment[]) => {
            // Use the last (most recent) non-empty segment
            const latest = [...segments].reverse().find((s) => s.text.trim());
            if (latest) setTranscript(latest.text);
          },
        );

        // ── Active speakers → derive agent Speaking state ──────────────────
        livekitRoom.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
          const agentSpeaking = speakers.some(
            (p) => p instanceof RemoteParticipant,
          );
          if (agentSpeaking) {
            setStatus('Speaking');
          } else {
            // Revert to Listening only if we were Speaking (not Thinking)
            setStatus((prev) => (prev === 'Speaking' ? 'Listening' : prev));
          }
        });

        // ── Room disconnected (agent farewell complete) ────────────────────
        livekitRoom.on(RoomEvent.Disconnected, () => {
          if (roomRef.current !== livekitRoom) return;
          stopPolling();
          setConfirmPending(false);
          setRoom(null);
          setSessionEnded(true);
          setMicEnabled(false);
        });

        // ── Connect (audio only) ───────────────────────────────────────────
        await livekitRoom.connect(url, token, { autoSubscribe: true });
        if (cancelled) {
          livekitRoom.disconnect();
          return;
        }

        // Unlock browser audio playback (required on many browsers for remote audio).
        try {
          await livekitRoom.startAudio();
        } catch {
          // startAudio may fail until user interacts; mic enable below often unlocks it.
        }

        // Enable microphone immediately after connecting
        await livekitRoom.localParticipant.setMicrophoneEnabled(true);
        try {
          await livekitRoom.startAudio();
        } catch {
          // Non-fatal — RoomAudioRenderer / track.attach may still succeed.
        }
        setMicEnabled(true);
        setRoom(livekitRoom);
        startPolling();
      } catch (err) {
        if (!cancelled) {
          const msg = err instanceof Error ? err.message : String(err);
          console.error('[LiveKit] Connection failed:', msg);
          setConnError(msg);
        }
      }
    };

    void connect();

    return () => {
      cancelled = true;
      stopPolling();
      setRoom(null);
      roomRef.current?.disconnect();
      roomRef.current = null;
    };
  }, [startPolling, stopPolling, sessionKey]);

  // ── New session reset ──────────────────────────────────────────────────────

  const handleNewSession = useCallback(() => {
    const currentRoom = roomRef.current;
    roomRef.current = null;
    if (currentRoom) {
      void currentRoom.disconnect();
    }
    setSessionEnded(false);
    setCart({ items: [], total: '$0.00', confirmed: false });
    setConfirmPending(false);
    setOrderId('');
    setTranscript('');
    setStatus('Listening');
    setAudioLevel(0);
    setConnError(null);
    // Increment key → useEffect re-runs → fresh room connection
    setSessionKey((k) => k + 1);
  }, []);

  // ── Microphone toggle ──────────────────────────────────────────────────────

  const handleMicToggle = useCallback(async () => {
    const room = roomRef.current;
    if (!room) return;

    const next = !micEnabled;

    try {
      if (next) {
        await room.startAudio();
      }
      await room.localParticipant.setMicrophoneEnabled(next);
      setMicEnabled(next);
      if (next) setStatus('Listening');
    } catch (err) {
      console.error('[LiveKit] Mic toggle failed:', err);
    }
  }, [micEnabled]);

  // ── Confirm order ──────────────────────────────────────────────────────────

  const handleConfirmOrder = useCallback(async () => {
    const room = roomRef.current;
    if (!room || cart.confirmed || confirmPending) return;

    try {
      setConfirmPending(true);
      setStatus('Thinking');
      const payload = new TextEncoder().encode(
        JSON.stringify({ type: 'confirm_order' }),
      );
      await room.localParticipant.publishData(payload, { reliable: true });
    } catch (err) {
      setConfirmPending(false);
      console.error('[LiveKit] publishData failed:', err);
    }
  }, [cart.confirmed, confirmPending]);

  useEffect(() => {
    if (cart.confirmed) {
      const timestamp = Date.now().toString(36).toUpperCase();
      const randomSuffix = Math.random().toString(36).slice(2, 6).toUpperCase();
      setOrderId(`FF-${timestamp}-${randomSuffix}`);
    } else {
      setOrderId('');
    }
  }, [cart.confirmed]);

  // ── Waveform heights driven by live audio level ────────────────────────────

  const barHeights = BASE_HEIGHTS.map((base) => {
    if (!micEnabled) return Math.round(base * 0.4);
    // audioLevel is 0–1; scale bars between 80 % and 300 % of base
    const scale = 0.8 + audioLevel * 2.2;
    return Math.round(base * scale);
  });

  // ─── Render ─────────────────────────────────────────────────────────────────

  // ── Session-ended confirmation screen ─────────────────────────────────────
  if (sessionEnded || cart.confirmed) {
    return (
      <main className="screen-shell" style={{ display: 'flex' }}>
        <div
          style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '32px',
            padding: '48px',
            textAlign: 'center',
          }}
        >
          {/* Reuse brand mark colours without touching CSS */}
          <div
            style={{
              width: '72px',
              height: '72px',
              borderRadius: '50%',
              border: '2px solid #e9a447',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              boxShadow: '0 0 40px rgba(233,164,71,0.25)',
            }}
          >
            <svg
              width="32"
              height="32"
              viewBox="0 0 24 24"
              fill="none"
              stroke="#e9a447"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <polyline points="20 6 9 17 4 12" />
            </svg>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <h2
              style={{
                color: '#ece5db',
                fontSize: 'clamp(1.4rem, 2.4vw, 2rem)',
                fontWeight: 700,
                margin: 0,
                letterSpacing: '-0.02em',
              }}
            >
              Order confirmed!
            </h2>
            <p
              style={{
                color: '#9b8f83',
                fontSize: '1rem',
                margin: 0,
                lineHeight: 1.6,
                maxWidth: '360px',
              }}
            >
              Thank you for choosing{' '}
              <span style={{ color: '#e9a447' }}>Forge &amp; Flame</span>. Your
              order is on its way.
            </p>
          </div>

          <div
            style={{
              width: 'min(100%, 520px)',
              border: '1px solid rgba(233,164,71,0.18)',
              borderRadius: '18px',
              padding: '18px 20px',
              background: 'rgba(18, 16, 14, 0.55)',
              boxShadow: '0 12px 40px rgba(0,0,0,0.18)',
              textAlign: 'left',
            }}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: '14px',
                gap: '12px',
              }}
            >
              <span
                style={{
                  color: '#9b8f83',
                  fontSize: '0.78rem',
                  letterSpacing: '0.08em',
                  textTransform: 'uppercase',
                }}
              >
                Order summary
              </span>
              <strong style={{ color: '#ece5db', fontSize: '1rem' }}>
                {cart.total}
              </strong>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              {cart.items.map((item, index) => (
                <div
                  key={`${item.name}-${index}`}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    gap: '16px',
                    paddingBottom: '10px',
                    borderBottom:
                      index === cart.items.length - 1
                        ? 'none'
                        : '1px solid rgba(255,255,255,0.08)',
                  }}
                >
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                    <span style={{ color: '#ece5db', fontWeight: 600 }}>
                      {item.quantity}× {item.name}
                    </span>
                    {item.modifiers ? (
                      <span style={{ color: '#9b8f83', fontSize: '0.9rem' }}>
                        {item.modifiers}
                      </span>
                    ) : null}
                  </div>
                  <strong style={{ color: '#e9a447', whiteSpace: 'nowrap' }}>
                    {item.price}
                  </strong>
                </div>
              ))}
            </div>
          </div>

          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: '6px',
              alignItems: 'center',
            }}
          >
            <span
              style={{
                color: '#9b8f83',
                fontSize: '0.82rem',
                letterSpacing: '0.08em',
                textTransform: 'uppercase',
              }}
            >
              Order ID
            </span>
            <strong
              style={{
                color: '#ece5db',
                fontSize: '1rem',
                letterSpacing: '0.12em',
              }}
            >
              {orderId}
            </strong>
          </div>

          <button
            className="confirm-button"
            type="button"
            style={{ maxWidth: '280px', minHeight: '60px', fontSize: '0.9rem' }}
            onClick={handleNewSession}
          >
            Start a new order
          </button>
        </div>
      </main>
    );
  }

  return (
    <main className="screen-shell">
      {room && <RoomAudioRenderer room={room} />}

      {/* ── Left: Voice Panel ─────────────────────────────────────────────── */}
      <section className="voice-panel" aria-label="Voice interface">
        <div className="brand-mark" aria-label="Restaurant logo">
          <span className="brand-ring" />
          <span className="brand-dot" />
        </div>

        <div className="voice-stage">

          {/* Connection error banner */}
          {connError && (
            <p
              style={{
                color: '#e57373',
                fontSize: '0.82rem',
                textAlign: 'center',
                maxWidth: '340px',
              }}
            >
              ⚠ {connError}
            </p>
          )}

          {/* Microphone button — toggles mic on/off */}
          <button
            className="mic-button"
            type="button"
            aria-label={micEnabled ? 'Mute microphone' : 'Unmute microphone'}
            aria-pressed={micEnabled}
            onClick={() => void handleMicToggle()}
          >
            <span className="mic-button__amber-ring" />
            <span className="mic-button__white-ring" />
            <span className="mic-button__plate">
              <img
                className="mic-icon"
                src={microphoneSvg}
                alt=""
                aria-hidden="true"
                style={{ opacity: micEnabled ? 1 : 0.45 }}
              />
            </span>
          </button>

          {/* Waveform — height & animation driven by live audio level */}
          <div className="waveform" aria-hidden="true">
            {barHeights.map((height, index) => (
              <span
                key={index}
                className="waveform-bar"
                style={{
                  ['--bar-height' as string]: `${height}px`,
                  animationDelay: `${index * 0.06}s`,
                  animationPlayState: micEnabled ? 'running' : 'paused',
                  opacity: micEnabled ? 1 : 0.3,
                }}
              />
            ))}
          </div>

          {/* Live transcript */}
          <p className="voice-transcript">
            {transcript ? `\u201C${transcript}\u201D` : ''}
          </p>

          {/* Agent status pill */}
          <div className={`status-pill status-pill--${status.toLowerCase()}`}>
            <span className="status-pill__dot" />
            <span>{status}</span>
          </div>

        </div>
      </section>

      {/* ── Right: Order Panel ────────────────────────────────────────────── */}
      <aside className="order-panel" aria-label="Live order cart">
        <div className="order-panel__header">
          <h2>Your order</h2>
        </div>

        <div className="order-panel__items">
          {cart.items.map((item, index) => (
            <article
              key={item.name}
              className="cart-item"
              style={{ ['--item-delay' as string]: `${index * 0.08}s` }}
            >
              <div className="cart-item__copy">
                <h3>
                  {item.quantity}× {item.name}
                </h3>
                <p>{item.modifiers}</p>
              </div>
              <div className="cart-item__price">{item.price}</div>
              <button
                className="cart-item__remove"
                type="button"
                aria-label={`Remove ${item.name}`}
              >
                ×
              </button>
            </article>
          ))}
        </div>

        <div className="order-panel__footer">
          <div className="total-row">
            <span>Total</span>
            <strong>{cart.total}</strong>
          </div>

          <button
            className={`confirm-button${confirmPending ? ' confirm-button--loading' : ''}`}
            type="button"
            onClick={() => void handleConfirmOrder()}
            disabled={cart.confirmed || confirmPending}
            aria-busy={confirmPending}
          >
            {confirmPending ? 'Confirming...' : 'Confirm order'}
          </button>

          <p className="order-note">
            Speak &quot;Cancel&quot; or &quot;Edit&quot; to modify your selection
          </p>
        </div>
      </aside>

    </main>
  );
}

export default App;
