// Minimal line icons (24x24, stroke-based) in the Tesla/Starlink idiom.
type P = { className?: string };
const base = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  viewBox: "0 0 24 24",
};

export const Play = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="M7 4.5v15l12-7.5z" />
  </svg>
);
export const Stop = ({ className }: P) => (
  <svg {...base} className={className}>
    <rect x="6" y="6" width="12" height="12" rx="2" />
  </svg>
);
export const Check = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="M4 12.5l5 5L20 6.5" />
  </svg>
);
export const Cross = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="M6 6l12 12M18 6L6 18" />
  </svg>
);
export const Chevron = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="M9 6l6 6-6 6" />
  </svg>
);
export const Antenna = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="M12 21V10M6 7a8.5 8.5 0 0 1 12 0M8.5 9.5a5 5 0 0 1 7 0" />
    <circle cx="12" cy="10" r="1.2" fill="currentColor" />
  </svg>
);
export const Beam = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="M4 12h8M12 12l6-4M12 12l6 4" />
    <circle cx="4" cy="12" r="1.5" fill="currentColor" />
  </svg>
);
export const Brain = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="M9 4a3 3 0 0 0-3 3v1a3 3 0 0 0-1 5.5A3 3 0 0 0 8 19h1V4zM15 4a3 3 0 0 1 3 3v1a3 3 0 0 1 1 5.5A3 3 0 0 1 16 19h-1V4z" />
  </svg>
);
export const Target = ({ className }: P) => (
  <svg {...base} className={className}>
    <circle cx="12" cy="12" r="8" />
    <circle cx="12" cy="12" r="3" />
    <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
  </svg>
);
export const Layers = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="M12 4l8 4-8 4-8-4zM4 12l8 4 8-4M4 16l8 4 8-4" />
  </svg>
);
export const Gear = ({ className }: P) => (
  <svg {...base} className={className}>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
  </svg>
);
export const Alert = ({ className }: P) => (
  <svg {...base} className={className}>
    <path d="M12 3l10 18H2zM12 10v5M12 18h.01" />
  </svg>
);
