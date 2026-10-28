/*
 src/components/Icons.tsx - small inline SVG icons (no icon library to download or maintain)
 ===========================================================================================
 One icon per pillar (Seedling = know-how, Scales = decisions, Shield = scams), plus the controls.
*/
const base = { width: 24, height: 24, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2,
  strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true } as const;

export const ArrowLeft = () => (<svg {...base}><path d="M19 12H5M11 6l-6 6 6 6" /></svg>);
export const Mic = () => (<svg {...base} width={38} height={38}><rect x="9" y="3" width="6" height="12" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></svg>);
export const Speaker = ({ off }: { off?: boolean }) => (
  <svg {...base}><path d="M4 9v6h4l5 4V5L8 9H4z" />{off ? <path d="M17 9l5 6M22 9l-5 6" /> : <path d="M16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12" />}</svg>);
export const Keyboard = () => (<svg {...base}><rect x="2" y="6" width="20" height="12" rx="2" /><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10" /></svg>);
export const Send = () => (<svg {...base}><path d="M5 12h14M13 6l6 6-6 6" /></svg>);
export const Seedling = () => (<svg {...base} width={30} height={30}><path d="M12 21v-9M12 12C12 7 8 5 4 5c0 4 3 7 8 7zM12 14c0-4 3-7 8-7 0 4-3 7-8 7z" /></svg>);
export const Scales = () => (<svg {...base} width={30} height={30}><path d="M12 3v18M7 21h10M5 7h14M5 7l-3 7a3.5 3.5 0 0 0 6 0L5 7zM19 7l-3 7a3.5 3.5 0 0 0 6 0l-3-7z" /></svg>);
export const Shield = () => (<svg {...base} width={30} height={30}><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z" /><path d="M9 12l2 2 4-4" /></svg>);
export const Database = () => (<svg {...base}><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" /></svg>);
export const Refresh = () => (<svg {...base}><path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7" /></svg>);
export const Calc = () => (<svg {...base}><rect x="5" y="3" width="14" height="18" rx="2" /><path d="M8 7h8M8 11h.01M12 11h.01M16 11h.01M8 15h.01M12 15h.01M16 15h.01" /></svg>);
export const Doc = () => (<svg {...base}><path d="M14 3H6v18h12V7l-4-4zM14 3v4h4M9 13h6M9 17h6" /></svg>);

export const PILLAR_ICON = { knowledge: Seedling, decisions: Scales, scams: Shield } as const;
