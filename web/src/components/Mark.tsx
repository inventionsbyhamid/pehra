export function Mark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="var(--accent)" />
      <path d="M5 17c3.2-5.4 6.8-8 11-8s7.8 2.6 11 8" fill="none" stroke="var(--accent-ink)" strokeWidth="2.6" strokeLinecap="round" />
      <circle cx="16" cy="19" r="4.2" fill="var(--accent-ink)" />
    </svg>
  );
}
