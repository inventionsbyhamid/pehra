// Rupees or approximate US dollars on screen. public/currency.js sets <html data-currency> before the page paints and
// CSS shows the matching amount, so the prerendered HTML is the same for everyone. Charges are always in rupees.
import { useSyncExternalStore } from "react";
import { dollars, rateDay, rupees, usdInr } from "./plans";

export type Currency = "inr" | "usd";

const KEY = "site.currency"; // public/currency.js reads the same key
const listeners = new Set<() => void>();

function current(): Currency {
  return document.documentElement.dataset.currency === "usd" ? "usd" : "inr";
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function setCurrency(next: Currency) {
  document.documentElement.dataset.currency = next;
  try {
    localStorage.setItem(KEY, next);
  } catch {
    // storage blocked: the choice lasts until the page closes
  }
  listeners.forEach((l) => l());
}

export function useCurrency(): Currency {
  return useSyncExternalStore(subscribe, current, () => "inr");
}

const OPTIONS: [Currency, string, string][] = [
  ["inr", "₹", " rupees"],
  ["usd", "$", " US dollars"],
];

export function CurrencySwitch() {
  const shown = useCurrency();
  return (
    <div className="cur-switch" role="group" aria-label="Show prices in">
      {OPTIONS.map(([id, sign, words]) => (
        <button key={id} type="button" aria-pressed={shown === id} onClick={() => setCurrency(id)}>
          {sign}
          <span className="visually-hidden">{words}</span>
        </button>
      ))}
    </div>
  );
}

export function DollarNote({ className = "" }: { className?: string }) {
  return (
    <p className={`cur-usd ${className}`.trim()}>
      Charged in rupees; dollar amounts are approximate, at ₹{usdInr.rate.toFixed(2)} to the dollar (
      <a href={usdInr.source_url}>RBI reference rate</a>, {rateDay}).
    </p>
  );
}

// an amount in rupees, or about dollars when the visitor shows dollars; each holds its own words so the hidden one
// never runs into the shown one
export function Money({ inr, before = "", after = "" }: { inr: number; before?: string; after?: string }) {
  return (
    <>
      <span className="cur-inr">
        {before}
        {rupees(inr)}
        {after && <span className="money-after">{after}</span>}
      </span>
      <span className="cur-usd">
        {before}
        {inr > 0 ? "about " : ""}
        {dollars(inr)}
        {after && <span className="money-after">{after}</span>}
      </span>
    </>
  );
}
