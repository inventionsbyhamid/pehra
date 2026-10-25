// Simple drawings of the product for the site: flat shapes, no gradients. Scenes are decorative (aria-hidden); the
// panels around them carry real text.
import type { ReactNode } from "react";

export function Mark({ className = "mark" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true" focusable="false" className={className}>
      <rect width="32" height="32" rx="7" fill="currentColor" />
      <path d="M10.5 8v16" stroke="#fff" strokeWidth="3" strokeLinecap="round" />
      <circle cx="16" cy="13.5" r="5.5" fill="none" stroke="#fff" strokeWidth="3" />
      <circle cx="16" cy="13.5" r="1.75" fill="#fff" />
    </svg>
  );
}

const ALERT = "#f08a7f"; // the severity accent as it reads on the dark wall
const BOX = "#9fb1ab";

function Scene({ children }: { children: ReactNode }) {
  return (
    <svg viewBox="0 0 160 100" aria-hidden="true" focusable="false" preserveAspectRatio="xMidYMid slice">
      {children}
    </svg>
  );
}

export function PoolScene() {
  return (
    <Scene>
      <rect width="160" height="100" fill="#26302e" />
      <rect x="14" y="30" width="134" height="62" rx="3" fill="#23475a" stroke="#3b4845" strokeWidth="3" />
      <path d="M16 51h130M16 71h130" stroke="#2c566b" strokeWidth="1.5" strokeDasharray="6 5" />
      <path d="M24 26V8M36 26V8M22 14h16M24 8h12" stroke="#7d8c87" strokeWidth="2" strokeLinecap="round" />
      <path d="M126 26V14h14v12" stroke="#4a5754" strokeWidth="2" fill="none" />
      <ellipse cx="106" cy="66" rx="9" ry="3.5" fill="#2f6179" />
      <circle cx="106" cy="60" r="4.5" fill="#a88a72" />
      <rect x="94" y="51" width="24" height="21" fill="none" stroke={ALERT} strokeWidth="2" />
    </Scene>
  );
}

export function GateScene() {
  return (
    <Scene>
      <rect width="160" height="100" fill="#28322f" />
      <rect y="62" width="160" height="38" fill="#323d3a" />
      <rect x="14" y="24" width="10" height="50" fill="#45514e" />
      <rect x="136" y="24" width="10" height="50" fill="#45514e" />
      <path d="M24 44L66 14" stroke="#cbd4d1" strokeWidth="3" strokeLinecap="round" />
      <rect x="58" y="44" width="48" height="28" rx="7" fill="#6f8188" />
      <rect x="64" y="47" width="36" height="11" rx="2" fill="#3a4b52" />
      <circle cx="66" cy="65" r="3" fill="#e4dec4" />
      <circle cx="98" cy="65" r="3" fill="#e4dec4" />
      <rect x="75" y="64" width="14" height="5" fill="#e6cf4f" />
      <rect x="60" y="72" width="8" height="5" fill="#1e2624" />
      <rect x="96" y="72" width="8" height="5" fill="#1e2624" />
      <rect x="54" y="40" width="56" height="40" fill="none" stroke={BOX} strokeWidth="1.5" />
      <path d="M8 88h144" stroke="#8fa39c" strokeWidth="1.5" strokeDasharray="5 4" />
      <path d="M146 84l6 4-6 4" stroke="#8fa39c" strokeWidth="1.5" fill="none" />
    </Scene>
  );
}

export function StaffGateScene() {
  return (
    <Scene>
      <rect width="160" height="100" fill="#2a3431" />
      <rect y="80" width="160" height="20" fill="#333e3b" />
      <rect x="56" y="16" width="48" height="64" fill="#1c2422" stroke="#45514e" strokeWidth="3" />
      <circle cx="80" cy="40" r="6" fill="#a88a72" />
      <rect x="70" y="47" width="20" height="33" rx="8" fill="#5d6a86" />
      <rect x="64" y="31" width="32" height="49" fill="none" stroke={BOX} strokeWidth="1.5" />
      <rect x="64" y="24" width="24" height="7" fill={BOX} />
    </Scene>
  );
}

export function ParkingScene() {
  const cars = [
    [8, 10, "#5f6e74"],
    [60, 10, "#7b6a5c"],
    [86, 10, "#4f5d6b"],
    [138, 10, "#8a8f88"],
    [34, 58, "#6a7470"],
    [112, 58, "#59646f"],
  ] as const;
  return (
    <Scene>
      <rect width="160" height="100" fill="#2a3432" />
      <path
        d="M4 6v36M30 6v36M56 6v36M82 6v36M108 6v36M134 6v36M160 6v36M4 56v38M30 56v38M56 56v38M82 56v38M108 56v38M134 56v38M160 56v38"
        stroke="#4a5653"
        strokeWidth="1.5"
      />
      {cars.map(([x, y, fill]) => (
        <rect key={`${x}${y}`} x={x} y={y} width="18" height="30" rx="5" fill={fill} />
      ))}
    </Scene>
  );
}

export function LobbyScene() {
  return (
    <Scene>
      <rect width="160" height="100" fill="#2c3634" />
      <rect y="74" width="160" height="26" fill="#353f3c" />
      <rect x="54" y="18" width="52" height="56" fill="#3d4845" />
      <rect x="58" y="22" width="21" height="52" fill="#6b7774" />
      <rect x="81" y="22" width="21" height="52" fill="#6b7774" />
      <rect x="74" y="9" width="12" height="6" fill="#45514e" />
      <rect x="20" y="58" width="14" height="16" fill="#4a5653" />
      <circle cx="27" cy="50" r="10" fill="#3e5a4c" />
      <rect x="118" y="58" width="30" height="12" rx="3" fill="#4d5866" />
    </Scene>
  );
}

// a still crop as Telegram gets it: the pool at night, the person boxed, their face blurred
export function PoolStill() {
  const tones = ["#8f7a69", "#a48a75", "#7d6b5d", "#9c836f", "#866f60", "#a18975", "#8a7464", "#987f6c", "#7f6a5b"];
  return (
    <svg viewBox="0 0 240 150" aria-hidden="true" focusable="false">
      <rect width="240" height="150" fill="#23475a" />
      <path d="M0 62h240M0 112h240" stroke="#2c566b" strokeWidth="3" strokeDasharray="12 10" />
      <rect y="0" width="240" height="30" fill="#3b4845" />
      <path d="M24 27V5M42 27V5M22 14h22M24 5h18" stroke="#8a9893" strokeWidth="3" strokeLinecap="round" />
      <ellipse cx="128" cy="98" rx="30" ry="10" fill="#2f6179" />
      {tones.map((fill, i) => (
        <rect key={i} x={113 + (i % 3) * 10} y={62 + Math.floor(i / 3) * 10} width="10" height="10" fill={fill} />
      ))}
      <rect x="92" y="50" width="72" height="62" fill="none" stroke="#e05a4c" strokeWidth="3" />
    </svg>
  );
}

export function Tick() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false" className="icon">
      <path d="M4.5 10.5l3.5 3.5 7.5-8" stroke="currentColor" strokeWidth="2.4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Plus() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false" className="icon faq-icon">
      <path d="M10 4v12M4 10h12" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
    </svg>
  );
}
