export const API =
  process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8080';

export type Job = {
  job_id: string;
  input_ref: string;
  input_type: string;
  status: string;
  stage: string | null;
  attempts: number;
  output: unknown | null;
  error: { stage?: string; code?: string; message?: string } | null;
  created_at: string;
  updated_at: string;
};

export const INPUT_TYPES = ['raw', 'compressed', 'legacy', 'structured'];

export async function listJobs(inputType?: string): Promise<Job[]> {
  const qs = new URLSearchParams({ limit: '50' });
  if (inputType) qs.set('input_type', inputType);
  const r = await fetch(`${API}/jobs?${qs}`, { cache: 'no-store' });
  if (!r.ok) throw new Error(`list failed: ${r.status}`);
  const d = await r.json();
  return d.jobs as Job[];
}

export async function getJob(id: string): Promise<Job> {
  const r = await fetch(`${API}/jobs/${id}`, { cache: 'no-store' });
  if (!r.ok) throw new Error(`get failed: ${r.status}`);
  return r.json();
}

export async function submitJob(inputRef: string, inputType: string) {
  const r = await fetch(`${API}/jobs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ input_ref: inputRef, input_type: inputType }),
  });
  if (!r.ok) throw new Error(`submit failed: ${r.status}`);
  return r.json();
}

export async function retryJob(id: string) {
  const r = await fetch(`${API}/jobs/${id}/retry`, { method: 'POST' });
  if (!r.ok) throw new Error(`retry failed: ${r.status}`);
  return r.json();
}

export async function cancelJob(id: string) {
  const r = await fetch(`${API}/jobs/${id}`, { method: 'DELETE' });
  if (!r.ok) throw new Error(`cancel failed: ${r.status}`);
  return r.json();
}
