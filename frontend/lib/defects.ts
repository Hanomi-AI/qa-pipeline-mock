// UI defect profile, mirroring the backend's DEFECTS env var.
// Set NEXT_PUBLIC_UI_DEFECTS="u1,u2" at build time. Empty = a correct UI.
const active = new Set(
  (process.env.NEXT_PUBLIC_UI_DEFECTS ?? '')
    .split(',')
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean),
);

export function uiDefect(id: string): boolean {
  return active.has(id);
}

export const UI_DEFECTS = [...active];
