import { useState } from "react";

// A value to hand on (an invite link, a connect command), with a button that copies it.
export function Copy({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }
  return (
    <div className="copy">
      <input className="input" readOnly value={value} aria-label={label} onFocus={(e) => e.currentTarget.select()} />
      <button type="button" className="btn" onClick={copy} aria-live="polite">{copied ? "Copied" : "Copy"}</button>
    </div>
  );
}
