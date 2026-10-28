/*
 src/components/Waveform.tsx - the calm wave lines above the microphone
 ======================================================================
 Decorative: gentle when idle, livelier while the person talks or the guide speaks. Pure CSS animation,
 switched off for people who asked their device for reduced motion. (Kept from the previous version.)
*/
function path(amp: number, phase: number): string {
  const pts: string[] = [];
  for (let x = 0; x <= 800; x += 10) pts.push(`${x === 0 ? "M" : "L"}${x} ${30 + Math.sin(x / 40 + phase) * amp}`);
  return pts.join(" ");
}
const LINES = [path(14, 0), path(10, 2), path(18, 4)];

export function Waveform({ active }: { active: boolean }) {
  return (
    <svg className={`wave${active ? " active" : ""}`} viewBox="0 0 400 60" preserveAspectRatio="none" aria-hidden="true">
      {LINES.map((d, i) => (<path key={i} d={d} className={`wl wl${i}`} />))}
    </svg>
  );
}
