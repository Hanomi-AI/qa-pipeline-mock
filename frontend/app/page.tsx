'use client';

import { useEffect, useState, useCallback } from 'react';
import Link from 'next/link';
import { INPUT_TYPES, listJobs, submitJob, type Job } from '../lib/api';
import { uiDefect } from '../lib/defects';

const STATUS_COLOR: Record<string, string> = {
  queued: '#7e888f',
  processing: '#0e6b74',
  completed: '#1c7c4a',
  failed: '#98303a',
  cancelled: '#545e68',
};

export default function Home() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [filter, setFilter] = useState('');
  const [inputRef, setInputRef] = useState('input_001.bin');
  const [inputType, setInputType] = useState('raw');
  const [err, setErr] = useState<string | null>(null);
  const [unfilteredCount, setUnfilteredCount] = useState(0);

  const refresh = useCallback(async () => {
    try {
      const rows = await listJobs(filter || undefined);
      setJobs(rows);
      if (uiDefect('u1')) {
        // u1: the count is fetched WITHOUT the filter, so the label disagrees
        // with the table whenever a filter is applied. Reads as a rounding or
        // caching quirk unless you assert both together.
        const all = await listJobs(undefined);
        setUnfilteredCount(all.length);
      }
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
  }, [filter]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 2000);
    return () => clearInterval(t);
  }, [refresh]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();

    // NOTE (deliberate): the row is added optimistically, before the server
    // confirms. A Playwright spec that asserts on the list immediately will
    // pass for the wrong reason. This is here on purpose.
    const optimistic: Job = {
      job_id: 'pending-' + Math.random().toString(36).slice(2, 8),
      input_ref: inputRef,
      input_type: inputType,
      status: 'queued',
      stage: null,
      attempts: 1,
      output: null,
      error: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    setJobs((j) => [...j, optimistic]);

    try {
      await submitJob(inputRef, inputType);
      await refresh();
    } catch (e) {
      setErr(String(e));
      setJobs((j) => j.filter((x) => x.job_id !== optimistic.job_id));
    }
  }

  return (
    <main>
      <section
        style={{
          border: '1px solid #d2d8dd',
          background: '#fff',
          padding: '1.1rem 1.25rem',
          marginBottom: '1.75rem',
        }}
      >
        <h2 style={{ margin: '0 0 0.85rem', fontSize: '0.95rem' }}>Submit a job</h2>
        <form onSubmit={onSubmit} data-testid="submit-form" style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap' }}>
          <input
            data-testid="input-ref"
            value={inputRef}
            onChange={(e) => setInputRef(e.target.value)}
            placeholder="input_ref"
            style={{ flex: '1 1 16rem', padding: '0.45rem 0.6rem', border: '1px solid #d2d8dd', fontFamily: 'monospace' }}
          />
          <select
            data-testid="input-type"
            value={inputType}
            onChange={(e) => setInputType(e.target.value)}
            style={{ padding: '0.45rem 0.6rem', border: '1px solid #d2d8dd' }}
          >
            {INPUT_TYPES.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
          <button
            data-testid="submit-job"
            type="submit"
            style={{ padding: '0.45rem 1rem', border: 'none', background: '#0e6b74', color: '#fff', cursor: 'pointer' }}
          >
            Submit
          </button>
        </form>
      </section>

      <section>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.75rem', marginBottom: '0.75rem' }}>
          <h2 style={{ margin: 0, fontSize: '0.95rem' }}>Jobs</h2>
          <select
            data-testid="filter-input-type"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            style={{ padding: '0.3rem 0.5rem', border: '1px solid #d2d8dd', fontSize: '0.85rem' }}
          >
            <option value="">all input types</option>
            {INPUT_TYPES.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
          <span data-testid="job-count" style={{ fontSize: '0.8rem', color: '#545e68' }}>
            {uiDefect('u1') ? unfilteredCount : jobs.length} shown
          </span>
        </div>

        {err && (
          <p data-testid="error" style={{ color: '#98303a', fontSize: '0.85rem' }}>{err}</p>
        )}

        <table data-testid="job-table" style={{ width: '100%', borderCollapse: 'collapse', background: '#fff', fontSize: '0.85rem' }}>
          <thead>
            <tr style={{ textAlign: 'left', borderBottom: '1px solid #15191d' }}>
              <th style={{ padding: '0.5rem 0.7rem', fontSize: '0.7rem', textTransform: 'uppercase', letterSpacing: '0.08em', color: '#7e888f' }}>Job</th>
              <th style={{ padding: '0.5rem 0.7rem', fontSize: '0.7rem', textTransform: 'uppercase', letterSpacing: '0.08em', color: '#7e888f' }}>Type</th>
              <th style={{ padding: '0.5rem 0.7rem', fontSize: '0.7rem', textTransform: 'uppercase', letterSpacing: '0.08em', color: '#7e888f' }}>Status</th>
              <th style={{ padding: '0.5rem 0.7rem', fontSize: '0.7rem', textTransform: 'uppercase', letterSpacing: '0.08em', color: '#7e888f' }}>Stage</th>
              <th style={{ padding: '0.5rem 0.7rem', fontSize: '0.7rem', textTransform: 'uppercase', letterSpacing: '0.08em', color: '#7e888f' }}>Att</th>
            </tr>
          </thead>
          <tbody>
            {jobs.length === 0 && (
              <tr><td colSpan={5} style={{ padding: '1rem 0.7rem', color: '#7e888f' }} data-testid="empty">No jobs yet.</td></tr>
            )}
            {jobs.map((j) => (
              <tr key={j.job_id} data-testid="job-row" data-job-status={j.status} style={{ borderBottom: '1px solid #e2e7ea' }}>
                <td style={{ padding: '0.5rem 0.7rem', fontFamily: 'monospace', fontSize: '0.78rem' }}>
                  {j.job_id.startsWith('pending-') ? (
                    <span style={{ color: '#7e888f' }}>{j.job_id}</span>
                  ) : (
                    <Link href={`/jobs/${j.job_id}`} data-testid="job-link" style={{ color: '#0e6b74' }}>
                      {j.job_id}
                    </Link>
                  )}
                </td>
                <td style={{ padding: '0.5rem 0.7rem' }}>{j.input_type}</td>
                <td style={{ padding: '0.5rem 0.7rem', color: STATUS_COLOR[j.status] ?? '#15191d', fontWeight: 600 }}>
                  {j.status}
                </td>
                <td style={{ padding: '0.5rem 0.7rem', color: '#545e68' }}>{j.stage ?? '—'}</td>
                <td style={{ padding: '0.5rem 0.7rem', fontVariantNumeric: 'tabular-nums' }}>{j.attempts}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </main>
  );
}
