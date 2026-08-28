export const metadata = {
  title: 'qa-pipeline-mock',
  description: 'System under test for the Hanomi QA take-home',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          fontFamily:
            'ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif',
          background: '#f4f6f7',
          color: '#15191d',
        }}
      >
        <div style={{ maxWidth: 960, margin: '0 auto', padding: '2rem 1.25rem 4rem' }}>
          <header style={{ borderBottom: '1px solid #d2d8dd', paddingBottom: '0.9rem', marginBottom: '1.75rem' }}>
            <h1 style={{ margin: 0, fontSize: '1.35rem', letterSpacing: '-0.02em' }}>
              qa-pipeline-mock
            </h1>
            <p style={{ margin: '0.3rem 0 0', fontSize: '0.85rem', color: '#545e68' }}>
              System under test. It misbehaves on purpose.
            </p>
          </header>
          {children}
        </div>
      </body>
    </html>
  );
}
