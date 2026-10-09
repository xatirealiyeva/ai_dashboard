import { useEffect, useState } from 'react';
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  Check,
  ChevronDown,
  CircleHelp,
  Database,
  FileText,
  Lightbulb,
  LoaderCircle,
  MessageSquareText,
  Radio,
  Send,
  Sparkles,
  Table2,
  WalletCards,
  X,
} from 'lucide-react';
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  Bar,
  BarChart,
} from 'recharts';

const samples = [
  'Son 6 ayda USD depozit satışlarının məbləğini və cari USD depozit portfelinin balansını müqayisə et',
  '2026-cı ildə satışların artım tempi ilə portfelin artım tempini aylıq müqayisə et',
  'VIP və qeyri-VIP müştərilər üzrə portfel balansı, müqavilə sayı və çəkili faiz dərəcəsi nədir?',
];

const palette = ['#2f6655', '#bb6848', '#91a94a', '#566f88'];
const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');

function apiUrl(path) {
  return `${apiBaseUrl}${path}`;
}

function websocketUrl(path) {
  const base = new URL(apiBaseUrl || window.location.origin);
  base.protocol = base.protocol === 'https:' ? 'wss:' : 'ws:';
  base.pathname = path;
  base.search = '';
  return base.toString();
}

function formatValue(value) {
  if (value === null || value === undefined) return '–';
  if (typeof value === 'number') {
    return new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(value);
  }
  return String(value);
}

function formatLabel(value) {
  return String(value)
    .replaceAll('_', ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatPeriod(value) {
  const date = Date.parse(value);
  return Number.isNaN(date)
    ? formatLabel(value)
    : new Intl.DateTimeFormat('en', { month: 'short', year: '2-digit' }).format(date);
}

function rowsAsObjects(result) {
  return (result?.rows || []).map((row) =>
    Object.fromEntries(result.columns.map((column, index) => [column, row[index]])),
  );
}

function metricValue(result) {
  const columns = result.columns || [];
  const row = result.rows?.[0] || [];
  const directIndex = columns.indexOf(result.metric);
  if (directIndex >= 0 && typeof row[directIndex] === 'number') {
    return { label: result.metric, value: row[directIndex] };
  }
  const excluded = /^(period|snapshot_date|prior_period_value|period_.*count|.*row_count)$/i;
  const numericIndexes = columns
    .map((column, index) => ({ column, index, value: row[index] }))
    .filter((item) => typeof item.value === 'number' && !excluded.test(item.column));
  const preferred = numericIndexes.find(({ column }) => /amount|balance|share|trend|pct|volume|rate/i.test(column));
  const selected = preferred || numericIndexes[0];
  if (selected) return { label: selected.column, value: selected.value };
  return { label: columns[0] || result.metric, value: row[0] };
}

function StatusPill({ connected, label }) {
  return (
    <span className={`status-pill ${connected ? 'is-online' : 'is-offline'}`}>
      <span className="status-dot" />
      {label}
    </span>
  );
}

function MetricCard({ result, index }) {
  const { value, label: outputLabel } = metricValue(result);
  const Icon = index % 2 === 0 ? WalletCards : Activity;
  return (
    <article className="metric-card">
      <div className="metric-card-top">
        <span className="metric-icon"><Icon size={17} strokeWidth={1.8} /></span>
        <span className="metric-caption">{formatLabel(result.metric || outputLabel)}</span>
        <span className="metric-period">{result.unit?.temporal_scope === 'latest_snapshot' ? 'LATEST SNAPSHOT' : 'QUERY RESULT'}</span>
      </div>
      <div className="metric-value">{formatValue(value)}</div>
      <div className="metric-foot">
        <span>{formatLabel(outputLabel)}</span>
        <span className="metric-trend"><ArrowUpRight size={13} /> Live</span>
      </div>
    </article>
  );
}

function ResultTable({ result }) {
  if (!result?.columns?.length) return <div className="empty-table">No tabular result returned.</div>;
  return (
    <div className="data-table-scroll">
      <table className="data-table">
        <thead><tr>{result.columns.map((column) => <th key={column}>{formatLabel(column)}</th>)}</tr></thead>
        <tbody>
          {result.rows.map((row, rowIndex) => (
            <tr key={`${result.id}-${rowIndex}`}>
              {row.map((value, cellIndex) => <td key={`${rowIndex}-${cellIndex}`}>{formatValue(value)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ChartPanel({ component, result }) {
  const data = rowsAsObjects(result).sort((left, right) => {
    const leftValue = left[component.x_key];
    const rightValue = right[component.x_key];
    const leftTime = Date.parse(leftValue);
    const rightTime = Date.parse(rightValue);
    return Number.isNaN(leftTime) || Number.isNaN(rightTime) ? 0 : leftTime - rightTime;
  });
  if (!data.length) return <div className="chart-empty">Sorğu bu seçim üçün sətir qaytarmadı.</div>;
  const chartType = component.type === 'bar' ? 'bar' : 'line';
  const valueKeys = component.series?.length ? component.series : [component.value_key];
  return (
    <div className="chart-wrap">
      <ResponsiveContainer width="100%" height="100%">
        {chartType === 'bar' ? (
          <BarChart data={data} margin={{ top: 10, right: 14, left: 4, bottom: 4 }}>
            <CartesianGrid stroke="#e8ece8" vertical={false} />
            <XAxis dataKey={component.x_key} tickLine={false} axisLine={false} tick={{ fill: '#77827c', fontSize: 11 }} tickFormatter={formatPeriod} />
            <YAxis tickLine={false} axisLine={false} tick={{ fill: '#77827c', fontSize: 10 }} tickFormatter={formatValue} width={68} />
            <Tooltip formatter={(value) => formatValue(value)} labelFormatter={formatPeriod} />
            {valueKeys.map((key, index) => <Bar key={key} dataKey={key} fill={palette[index % palette.length]} radius={[3, 3, 0, 0]} maxBarSize={34} />)}
          </BarChart>
        ) : (
          <LineChart data={data} margin={{ top: 12, right: 14, left: 4, bottom: 4 }}>
            <CartesianGrid stroke="#e8ece8" vertical={false} />
            <XAxis dataKey={component.x_key} tickLine={false} axisLine={false} tick={{ fill: '#77827c', fontSize: 11 }} tickFormatter={formatPeriod} />
            <YAxis tickLine={false} axisLine={false} tick={{ fill: '#77827c', fontSize: 10 }} tickFormatter={formatValue} width={68} />
            <Tooltip formatter={(value) => formatValue(value)} labelFormatter={formatPeriod} />
            {valueKeys.map((key, index) => <Line key={key} type="monotone" dataKey={key} stroke={palette[index % palette.length]} strokeWidth={2.2} dot={{ r: 2.5, fill: palette[index % palette.length], strokeWidth: 0 }} activeDot={{ r: 4 }} />)}
          </LineChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}

function DashboardComponent({ component, result, index, viewMode }) {
  if (!result) return null;
  const isChart = ['bar', 'line'].includes(component.type) && viewMode === 'chart';
  return (
    <article className="visual-card">
      <div className="visual-heading">
        <div>
          <p className="visual-overline">{formatLabel(result.metric || `Result ${index + 1}`)}</p>
          <h3>{component.title || formatLabel(result.metric)}</h3>
        </div>
        <span className="visual-type">{isChart ? (component.type === 'bar' ? 'BAR ANALYSIS' : 'MONTHLY TREND') : `${result.rows?.length || 0} ROWS`}</span>
      </div>
      {isChart ? <ChartPanel component={component} result={result} /> : <ResultTable result={result} />}
    </article>
  );
}

function App() {
  const [socketState, setSocketState] = useState('connecting');
  const [health, setHealth] = useState({ qwen_configured: false, database_available: false });
  const [question, setQuestion] = useState('');
  const [activeQuestion, setActiveQuestion] = useState('');
  const [response, setResponse] = useState(null);
  const [loading, setLoading] = useState(false);
  const [viewMode, setViewMode] = useState('chart');
  const [showSql, setShowSql] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let socket;
    let retryTimer;
    let disposed = false;

    fetch(apiUrl('/health'))
      .then((res) => res.json())
      .then(setHealth)
      .catch(() => setHealth({ qwen_configured: false, database_available: false }));

    const connect = () => {
      if (disposed) return;
      socket = new WebSocket(websocketUrl('/ws'));
      socket.onopen = () => setSocketState('connected');
      socket.onmessage = (event) => {
        setLoading(false);
        try {
          const payload = JSON.parse(event.data);
          if (payload.type === 'error') setError(payload.message || 'Sorğu icra olunmadı.');
          else {
            setError('');
            setResponse(payload);
          }
        } catch {
          setError('Server cavabı oxuna bilmədi.');
        }
      };
      socket.onerror = () => setSocketState('error');
      socket.onclose = () => {
        setSocketState('disconnected');
        if (!disposed) retryTimer = window.setTimeout(connect, 2000);
      };
      window.depositDeskSocket = socket;
    };
    connect();

    return () => {
      disposed = true;
      window.clearTimeout(retryTimer);
      socket?.close();
      delete window.depositDeskSocket;
    };
  }, []);

  const successfulResults = (response?.results || []).filter((item) => item.type === 'result');
  const insightList = response?.synthesis?.insights || response?.insights || [];
  const components = response?.dashboard?.components || [];
  const generatedAt = new Date().toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });

  function ask(value = question) {
    const text = value.trim();
    const socket = window.depositDeskSocket;
    if (!text || socket?.readyState !== WebSocket.OPEN || loading) return;
    setQuestion(text);
    setActiveQuestion(text);
    setLoading(true);
    setError('');
    setShowSql(false);
    socket.send(JSON.stringify({ question: text }));
  }

  function handleSubmit(event) {
    event.preventDefault();
    ask();
  }

  const displayComponents = components.length
    ? components
    : successfulResults.map((result, index) => ({ type: 'table', title: result.metric || `Result ${index + 1}`, data_index: index }));
  const chartable = displayComponents.some((component) => ['line', 'bar'].includes(component.type));

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand-lockup" href="#top" aria-label="AI Dashboard home">
          <span className="brand-symbol"><span /></span>
          <span className="brand-copy"><strong>AI DASHBOARD</strong><small>DATA & INTELLIGENCE</small></span>
        </a>
        <div className="side-section-label">WORKSPACE</div>
        <nav className="side-nav" aria-label="Main navigation">
          <a className="nav-item active" href="#overview"><BarChart3 size={17} /><span>Analytics overview</span></a>
          <a className="nav-item" href="#query"><MessageSquareText size={17} /><span>Query studio</span><span className="nav-shortcut">Q</span></a>
          <a className="nav-item" href="#insights"><Lightbulb size={17} /><span>Insights</span></a>
        </nav>
        <div className="sidebar-spacer" />
        <div className="side-status-block">
          <div className="side-section-label">SYSTEM STATUS</div>
          <div className="side-status-row"><span><Radio size={14} /> API gateway</span><i className={socketState === 'connected' ? 'good' : 'bad'} /></div>
          <div className="side-status-row"><span><Database size={14} /> Analytics DB</span><i className={health.database_available ? 'good' : 'bad'} /></div>
        </div>
        <div className="sidebar-user">
          <span className="user-avatar">AD</span>
          <span><strong>Analytics Desk</strong><small>Internal workspace</small></span>
          <ChevronDown size={15} />
        </div>
      </aside>

      <main className="main-area" id="top">
        <header className="topbar">
          <div className="breadcrumb">Workspace <span>/</span> Analytics overview</div>
          <div className="topbar-right">
            <span className="secure-label"><span /> INTERNAL DATA</span>
            <StatusPill connected={socketState === 'connected'} label={socketState === 'connected' ? 'Live connection' : 'Reconnecting'} />
          </div>
        </header>

        <div className="content" id="overview">
          <section className="page-heading">
            <div>
              <p className="eyebrow">DEPOSIT PORTFOLIO / ANALYTICS</p>
              <h1>Portfolio intelligence</h1>
              <p className="page-subtitle">Explore deposit performance with natural-language analysis and decision-ready insights.</p>
            </div>
            <div className="updated-stamp"><span className="stamp-label">WORKSPACE TIME</span><strong>{generatedAt}</strong></div>
          </section>

          <section className="query-section" id="query">
            <div className="query-heading">
              <div className="query-title"><span className="query-icon"><Sparkles size={16} /></span><div><h2>Ask your data</h2><p>Questions are matched to approved deposit metrics.</p></div></div>
              <span className="query-agent"><span /> QWEN ANALYTICS</span>
            </div>
            <form className="query-form" onSubmit={handleSubmit}>
              <textarea value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); ask(); } }} placeholder="Ask about balances, sales, renewal trends or customer segments…" aria-label="Ask a deposit analytics question" />
              <button className="submit-button" type="submit" disabled={loading || socketState !== 'connected' || !question.trim()}>
                {loading ? <LoaderCircle size={16} className="spin" /> : <Send size={15} />}
                <span>{loading ? 'Analyzing' : 'Run analysis'}</span>
              </button>
            </form>
            <div className="sample-row"><span className="sample-caption">TRY</span>{samples.map((sample, index) => <button key={sample} className="sample-chip" onClick={() => ask(sample)} disabled={loading || socketState !== 'connected'}>{['Sales + balance comparison', 'Monthly growth', 'VIP concentration'][index]}</button>)}</div>
          </section>

          {error && <div className="error-banner"><X size={16} /><span>{error}</span></div>}
          {loading && <div className="loading-strip"><LoaderCircle size={16} className="spin" /><span>Agents are decomposing, matching, validating and analyzing the request…</span></div>}

          {response && !loading && (
            <>
              <section className="answer-header">
                <div><p className="eyebrow">ANALYSIS RESULT</p><h2>{response.synthesis?.dashboard?.title || 'Query results'}</h2><p className="answer-question"><Check size={14} /> {activeQuestion}</p></div>
                <span className="request-reference">REF / {response.request_id}</span>
              </section>

              {response.synthesis?.summary && <section className="summary-band"><span className="summary-mark"><Sparkles size={17} /></span><div><span className="summary-label">EXECUTIVE SUMMARY</span><p>{response.synthesis.summary}</p></div></section>}

              <section className="metric-grid" aria-label="Key metrics">
                {successfulResults.slice(0, 4).map((result, index) => <MetricCard key={`${result.id}-${result.metric}`} result={result} index={index} />)}
                {!successfulResults.length && <div className="empty-state-inline">No supported metrics were returned.</div>}
              </section>

              <section className="visual-section">
                <div className="section-heading">
                  <div><p className="eyebrow">RESULT EXPLORER</p><h2>Performance detail</h2></div>
                  <div className="view-switch" role="group" aria-label="Result view">
                    <button className={viewMode === 'chart' ? 'selected' : ''} onClick={() => setViewMode('chart')} disabled={!chartable}><Activity size={15} /> Charts</button>
                    <button className={viewMode === 'table' ? 'selected' : ''} onClick={() => setViewMode('table')}><Table2 size={15} /> Tables</button>
                  </div>
                </div>
                <div className="visual-grid">
                  {displayComponents.map((component, index) => <DashboardComponent key={`${component.data_index}-${index}`} component={component} result={successfulResults[component.data_index]} index={index} viewMode={viewMode} />)}
                </div>
              </section>

              <section className="insights-section" id="insights">
                <div className="section-heading"><div><p className="eyebrow">SYNTHESIZER / QWEN</p><h2>What stands out</h2></div><span className="insight-count">{insightList.length} INSIGHTS</span></div>
                <div className="insight-grid">
                  {insightList.map((insight, index) => <article className="insight-card" key={`${index}-${insight}`}><span className="insight-index">0{index + 1}</span><p>{insight}</p><span className="insight-source"><Sparkles size={12} /> DATA-BASED OBSERVATION</span></article>)}
                  {!insightList.length && <div className="empty-insight"><CircleHelp size={17} /> No additional insights were returned for this query.</div>}
                </div>
                {successfulResults.some((result) => result.warnings?.length) && <p className="warning-note"><CircleHelp size={14} /> {successfulResults.flatMap((result) => result.warnings || []).join(' · ')}</p>}
              </section>

              <section className="provenance-section">
                <button className="provenance-toggle" onClick={() => setShowSql(!showSql)}><span><FileText size={15} /> Query provenance</span><ChevronDown className={showSql ? 'rotated' : ''} size={16} /></button>
                {showSql && <div className="sql-list">{successfulResults.map((result) => <div key={result.id} className="sql-entry"><div><strong>{formatLabel(result.metric)}</strong><span>{result.semantic_query?.metric}</span></div><pre>{result.sql}</pre></div>)}</div>}
              </section>
            </>
          )}

          {!response && !loading && <section className="welcome-panel"><div className="welcome-art"><div className="art-grid"><span /><span /><span /><span /><span /><span /><span /><span /><span /></div><div className="art-line" /></div><div className="welcome-copy"><p className="eyebrow">ANALYTICS WORKSPACE</p><h2>Clarity for every deposit decision.</h2><p>Ask a question to turn portfolio, sales and renewal data into a clear view of performance.</p><div className="welcome-features"><span><Check size={14} /> Governed metrics</span><span><Check size={14} /> Read-only analytics</span><span><Check size={14} /> Synthesized insights</span></div></div></section>}

          <footer className="page-footer"><span>AI DASHBOARD · INTERNAL ANALYTICS</span><span>READ-ONLY DATA ACCESS</span></footer>
        </div>
      </main>
    </div>
  );
}

export default App;
