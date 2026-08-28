'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { cancelJob, getJob, retryJob, type Job } from '../../../lib/api';
import { uiDefect } from '../../../lib/defects';

const TERMINAL = ['completed', 'failed', 'cancelled'];

// u2: only 'completed' stops the poll, so a failed or cancelled job is polled
// forever - a leaked timer and endless requests. Invisible unless you watch the
// network tab or assert that polling stops.
const STOPS_POLLING = uiDefect('u2') ? ['completed'] : TERMINAL;

export default function JobDetail() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [job, setJob] = useState<Job | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setInterval> | null = null;

    async function tick() {
      try {
        const j = await getJob(id);
        if (!alive) return;
        setJob(j);
        setErr(null);
        // stop polling once the job can no longer change
        if (STOPS_POLLING.includes(j.status) && timer) {
          clearInterval(timer);
          timer = null;
        }
      } catch (e) {
        if (alive) setErr(String(e));
      }
    }

    tick();
    timer = setInterval(tick, 1500);
    return () => {
      alive = false;
      if (timer) clearInterval(timer);
    };
  }, [id]);

  async function act(fn: (id: string) => Promise<unknown>) {
    try {
      await fn(id);
      setJob(await getJob(id));
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
  }

  return (
    <main>
      <p style={{ fontSize: '0.85rem', marginTop: 0 }}>
        <Link href="/" data-testid="back" style={{ color: '#0e6b74' }}>← all jobs</Link>
      </p>

      <h2 style={{ fontFamily: 'monospace', fontSize: '1rem', margin: '0 0 1rem' }} data-testid="job-id">
        {id}
      </h2>

      {err && <p data-testid="error" style={{ color: '#98303a', fontSize: '0.85rem' }}>{err}</p>}
      {!job && !err && <p data-testid="loading" style={{ color: '#7e888f' }}>Loading…</p>}

      {job && (
        <>
          <dl
            data-testid="job-summary"
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(9rem, 1fr))',
              gap: 0,
              border: '1px solid #d2d8dd',
              background: '#fff',
              margin: '0 0 1.5rem',
            }}
          >
            {[
              ['status', job.status],
              ['stage', job.stage ?? '—'],
              ['input type', job.input_type],
              ['attempts', String(job.attempts)],
            ].map(([k, v]) => (
              <div key={k} style={{ padding: '0.8rem 1rem', borderRight: '1px solid #e2e7ea' }}>
                <dt style={{ fontSize: '0.62rem', textTransform: 'uppercase', letterSpacing: '0.1em', color: '#7e888f', margin: '0 0 0.25rem' }}>
                  {k}
                </dt>
                <dd data-testid={`field-${String(k).replace(' ', '-')}`} style={{ margin: 0, fontWeight: 600, fontSize: '0.9rem' }}>
                  {v}
                </dd>
              </div>
            ))}
          </dl>

          <div style={{ display: 'flex', gap: '0.6rem', marginBottom: '1.5rem' }}>
            <button
              data-testid="retry"
              onClick={() => act(retryJob)}
              disabled={!TERMINAL.includes(job.status)}
              style={{ padding: '0.4rem 0.9rem', border: '1px solid #0e6b74', background: '#fff', color: '#0e6b74', cursor: 'pointer' }}
            >
              Retry
            </button>
            <button
              data-testid="cancel"
              onClick={() => act(cancelJob)}
              // u3: stays clickable on a terminal job, so the click 409s and
              // surfaces a raw error instead of being prevented.
              disabled={uiDefect('u3') ? false : TERMINAL.includes(job.status)}
              style={{ padding: '0.4rem 0.9rem', border: '1px solid #98303a', background: '#fff', color: '#98303a', cursor: 'pointer' }}
            >
              Cancel
            </button>
          </div>

          {job.error && (
            <section style={{ marginBottom: '1.5rem' }}>
              <h3 style={{ fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '0.1em', color: '#98303a' }}>Error</h3>
              <pre data-testid="job-error" style={{ background: '#fff', border: '1px solid #d2d8dd', borderLeft: '2px solid #98303a', padding: '0.9rem', overflowX: 'auto', fontSize: '0.78rem' }}>
                {JSON.stringify(job.error, null, 2)}
              </pre>
            </section>
          )}

          <section>
            <h3 style={{ fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '0.1em', color: '#545e68' }}>Output</h3>
            {job.output ? (
              <pre data-testid="job-output" style={{ background: '#fff', border: '1px solid #d2d8dd', borderLeft: '2px solid #0e6b74', padding: '0.9rem', overflowX: 'auto', fontSize: '0.78rem', maxHeight: '30rem' }}>
                {JSON.stringify(job.output, null, 2)}
              </pre>
            ) : (
              <p data-testid="no-output" style={{ color: '#7e888f', fontSize: '0.85rem' }}>
                No output yet.
              </p>
            )}
          </section>
        </>
      )}
    </main>
  );
}
