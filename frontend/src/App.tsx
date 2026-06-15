import { useMemo, useState } from 'react';
import microphoneSvg from './microphone-svgrepo-com.svg';

const waveformHeights = [
  26, 48, 34, 19, 42, 24, 37, 28, 31, 17, 45, 23, 39, 22, 29, 34, 18, 46, 25, 32, 21, 40, 27,
  30,
];

const cartItems = [
  {
    name: 'Wild Mushroom Risotto',
    modifiers: 'Extra truffle shavings',
    price: '$38.00',
  },
  {
    name: 'Barolo, G.D. Vajra 2018',
    modifiers: 'Serve at 18°C',
    price: '$24.00',
  },
];

const transcriptText = '“I’ll have the truffle-infused risotto with a glass of 2018 vintage Barolo”';

function App() {
  const [status, setStatus] = useState<'Listening' | 'Thinking' | 'Speaking'>('Listening');
  const [isConfirming, setIsConfirming] = useState(false);

  const total = useMemo(() => '$62.00', []);

  const cycleStatus = () => {
    setStatus((currentStatus) => {
      if (currentStatus === 'Listening') {
        return 'Thinking';
      }

      if (currentStatus === 'Thinking') {
        return 'Speaking';
      }

      return 'Listening';
    });
  };

  const handleConfirmOrder = () => {
    if (isConfirming) {
      return;
    }

    setIsConfirming(true);
    setStatus('Thinking');

    window.setTimeout(() => {
      setStatus('Speaking');
      setIsConfirming(false);
    }, 1400);
  };

  return (
    <main className="screen-shell">
      <section className="voice-panel" aria-label="Voice interface">
        <div className="brand-mark" aria-label="Restaurant logo">
          <span className="brand-ring" />
          <span className="brand-dot" />
        </div>

        <div className="voice-stage">
          <button className="mic-button" type="button" aria-label="Microphone button" onClick={cycleStatus}>
            <span className="mic-button__amber-ring" />
            <span className="mic-button__white-ring" />
            <span className="mic-button__plate">
              <img className="mic-icon" src={microphoneSvg} alt="" aria-hidden="true" />
            </span>
          </button>

          <div className="waveform" aria-hidden="true">
            {waveformHeights.map((height, index) => (
              <span
                key={`${height}-${index}`}
                className="waveform-bar"
                style={{ ['--bar-height' as string]: `${height}px`, animationDelay: `${index * 0.06}s` }}
              />
            ))}
          </div>

          <p className="voice-transcript">{transcriptText}</p>

          <div className={`status-pill status-pill--${status.toLowerCase()}`}>
            <span className="status-pill__dot" />
            <span>{status}</span>
          </div>
        </div>
      </section>

      <aside className="order-panel" aria-label="Live order cart">
        <div className="order-panel__header">
          <h2>Your order</h2>
        </div>

        <div className="order-panel__items">
          {cartItems.map((item, index) => (
            <article
              key={item.name}
              className="cart-item"
              style={{ ['--item-delay' as string]: `${index * 0.08}s` }}
            >
              <div className="cart-item__copy">
                <h3>{item.name}</h3>
                <p>{item.modifiers}</p>
              </div>
              <div className="cart-item__price">{item.price}</div>
              <button className="cart-item__remove" type="button" aria-label={`Remove ${item.name}`}>
                ×
              </button>
            </article>
          ))}
        </div>

        <div className="order-panel__footer">
          <div className="total-row">
            <span>Total</span>
            <strong>{total}</strong>
          </div>

          <button
            className={`confirm-button${isConfirming ? ' confirm-button--loading' : ''}`}
            type="button"
            onClick={handleConfirmOrder}
            disabled={isConfirming}
            aria-busy={isConfirming}
          >
            {isConfirming ? 'Confirming...' : 'Confirm order'}
          </button>

          <p className="order-note">Speak &quot;Cancel&quot; or &quot;Edit&quot; to modify your selection</p>
        </div>
      </aside>
    </main>
  );
}

export default App;
