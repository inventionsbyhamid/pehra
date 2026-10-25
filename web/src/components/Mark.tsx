// The brand mark, drawn as on the public site (site/art.tsx), in the brand colour.
export function Mark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false" style={{ color: "var(--brand)" }}>
      <rect width="32" height="32" rx="7" fill="currentColor" />
      <path d="M10.5 8v16" stroke="#fff" strokeWidth="3" strokeLinecap="round" />
      <circle cx="16" cy="13.5" r="5.5" fill="none" stroke="#fff" strokeWidth="3" />
      <circle cx="16" cy="13.5" r="1.75" fill="#fff" />
    </svg>
  );
}
