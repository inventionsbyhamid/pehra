import { useId } from "react";

// A small set of choices as radio buttons that look like buttons; arrow keys move between them.
export function Choices<T extends string>({ legend, value, options, onChange, hideLegend }: {
  legend: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  hideLegend?: boolean;
}) {
  const name = useId();
  return (
    <fieldset className="choices">
      <legend className={hideLegend ? "sr-only" : "label"}>{legend}</legend>
      {options.map((o) => (
        <label key={o.value}>
          <input type="radio" name={name} value={o.value} checked={value === o.value} onChange={() => onChange(o.value)} />
          {o.label}
        </label>
      ))}
    </fieldset>
  );
}
