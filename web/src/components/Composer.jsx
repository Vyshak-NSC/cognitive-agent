import { useEffect, useRef } from "react";

export default function Composer({ value, onChange, onSend, busy, disabledReason, providerLabel }) {
  const box = useRef(null);

  // Grow with the text, up to a limit.
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [value]);

  const submit = (e) => {
    e.preventDefault();
    onSend();
  };

  return (
    <form className="composer" onSubmit={submit}>
      {disabledReason && <p className="notice">{disabledReason}</p>}
      <div className="composer-row">
        <label className="sr-only" htmlFor="message">
          Message
        </label>
        <textarea
          id="message"
          ref={box}
          rows={1}
          value={value}
          placeholder={providerLabel ? `Message ${providerLabel}` : "Message"}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              onSend();
            }
          }}
        />
        <button type="submit" className="primary" disabled={busy || !value.trim() || Boolean(disabledReason)}>
          Send message
        </button>
      </div>
      <p className="hint">Enter sends. Shift and Enter adds a new line.</p>
    </form>
  );
}
