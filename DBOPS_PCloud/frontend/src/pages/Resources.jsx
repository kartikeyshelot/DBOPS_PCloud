import React, { useState, useMemo } from 'react'
import { useResourceServers, useFleetSummary, useActionItems } from '../hooks/useData'
import DrillDown from '../components/DrillDown'

/* ── Tiny helper: colour a utilisation bar by severity ─────────────────── */
function utilColor(pct) {
  if (pct >= 90) return 'var(--critical)'
  if (pct >= 75) return 'var(--warning)'
  if (pct >= 40) return 'var(--amber)'
  return 'var(--success)'
}

function utilBg(pct) {
  if (pct >= 90) return 'var(--critical-bg)'
  if (pct >= 75) return 'var(--warning-bg)'
  return 'rgba(28,20,16,0.05)'
}

function priorityBadge(pri) {
  const cls = {
    URGENT: 'badge-urgent', HIGH: 'badge-high',
    MEDIUM: 'badge-medium', LOW: 'badge-low', NONE: 'badge-none',
  }
  return <span className={`badge ${cls[pri] || 'badge-none'}`}>{pri}</span>
}

function fmtSize(gb) {
  if (gb >= 1024) return `${(gb / 1024).toFixed(1)} TB`
  if (gb >= 1) return `${gb.toFixed(1)} GB`
  return `${(gb * 1024).toFixed(0)} MB`
}

function fmtDays(d) {
  if (d >= 9999) return '—'
  if (d >= 365) return `${(d / 365).toFixed(1)}y`
  return `${d}d`
}

/* ══════════════════════════════════════════════════════════════════════════
   MINI BAR — shows provisioned vs utilised as a thin progress bar + numbers
══════════════════════════════════════════════════════════════════════════ */
function MiniBar({ used, total, unit = '', pct = null, width = 56 }) {
  const p = pct != null ? pct : (total > 0 ? (used / total) * 100 : 0)
  return (
    <div style={{ minWidth: width }}>
      <div style={{
        fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, fontWeight: 600,
        color: p > 90 ? 'var(--critical)' : p > 75 ? 'var(--warning)' : 'var(--text-primary)',
        marginBottom: 3,
      }}>
        {total > 0 ? (
          <>
            <span>{typeof used === 'number' ? (used % 1 === 0 ? used : used.toFixed(1)) : used}</span>
            <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}> / {typeof total === 'number' ? (total % 1 === 0 ? total : total.toFixed(1)) : total}{unit ? ` ${unit}` : ''}</span>
          </>
        ) : (
          <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>—</span>
        )}
      </div>
      {total > 0 && (
        <div style={{
          height: 4, borderRadius: 2, background: 'rgba(28,20,16,0.07)', overflow: 'hidden',
        }}>
          <div style={{
            height: '100%', borderRadius: 2,
            width: `${Math.min(p, 100)}%`,
            background: utilColor(p),
            transition: 'width 0.5s ease',
          }} />
        </div>
      )}
      {total > 0 && (
        <div style={{
          fontFamily: "'IBM Plex Mono', monospace", fontSize: 9,
          color: 'var(--text-muted)', marginTop: 2,
        }}>
          {p.toFixed(0)}%
        </div>
      )}
    </div>
  )
}


/* ══════════════════════════════════════════════════════════════════════════
   FLEET SUMMARY BAR — big numbers across the top
══════════════════════════════════════════════════════════════════════════ */
function FleetBar({ fleet }) {
  if (!fleet) return null

  const cards = [
    {
      label: 'Servers',
      value: fleet.server_count,
      sub: `${fleet.by_environment?.length || 0} environments`,
    },
    {
      label: 'vCPUs',
      value: `${fleet.cpu_used} / ${fleet.cpu_total}`,
      sub: `${fleet.cpu_avg_pct}% avg utilisation`,
      pct: fleet.cpu_avg_pct,
    },
    {
      label: 'RAM',
      value: `${fmtSize(fleet.ram_used_gb)} / ${fmtSize(fleet.ram_total_gb)}`,
      sub: `${fleet.ram_avg_pct}% avg utilisation`,
      pct: fleet.ram_avg_pct,
    },
    {
      label: 'Disk',
      value: `${fmtSize(fleet.disk_used_gb)} / ${fmtSize(fleet.disk_total_gb)}`,
      sub: `${fmtSize(fleet.disk_free_gb)} free`,
      pct: fleet.disk_avg_pct,
    },
    {
      label: 'Databases',
      value: fleet.db_count,
      sub: fleet.db_growth_gb_day > 0
        ? `+${fleet.db_growth_gb_day.toFixed(1)} GB/day growth`
        : 'Stable',
    },
  ]

  return (
    <div className="stagger-in" style={{
      display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 8, marginBottom: 16,
    }}>
      {cards.map((c, i) => (
        <div key={i} className="kpi-card">
          <div className="kpi-label">{c.label}</div>
          <div className="kpi-value" style={{ fontSize: 18 }}>{c.value}</div>
          {c.pct != null && (
            <div style={{
              height: 3, borderRadius: 2, background: 'rgba(28,20,16,0.07)',
              margin: '8px 0 4px', overflow: 'hidden',
            }}>
              <div style={{
                height: '100%', borderRadius: 2, width: `${Math.min(c.pct, 100)}%`,
                background: utilColor(c.pct), transition: 'width 0.6s ease',
              }} />
            </div>
          )}
          <div className="kpi-sub">{c.sub}</div>
        </div>
      ))}
    </div>
  )
}


/* ══════════════════════════════════════════════════════════════════════════
   ENVIRONMENT COMPARISON — side by side
══════════════════════════════════════════════════════════════════════════ */
function EnvComparison({ envData }) {
  if (!envData || envData.length < 2) return null

  return (
    <div className="card" style={{ padding: 16, marginBottom: 16 }}>
      <div className="section-hdr" style={{ marginBottom: 10 }}>
        <div>
          <h3 className="section-title">Environment Comparison</h3>
          <p className="section-sub">Resource density and utilisation by environment</p>
        </div>
      </div>
      <table className="data-table" style={{ fontSize: 12 }}>
        <thead>
          <tr>
            <th>Environment</th>
            <th style={{ textAlign: 'right' }}>Servers</th>
            <th style={{ textAlign: 'right' }}>Total vCPU</th>
            <th style={{ textAlign: 'right' }}>Avg CPU %</th>
            <th style={{ textAlign: 'right' }}>Total RAM</th>
            <th style={{ textAlign: 'right' }}>Avg RAM %</th>
            <th style={{ textAlign: 'right' }}>Total Disk</th>
            <th style={{ textAlign: 'right' }}>Disk Used</th>
          </tr>
        </thead>
        <tbody>
          {envData.map(e => (
            <tr key={e.environment}>
              <td style={{ fontWeight: 600 }}>{e.environment}</td>
              <td style={{ textAlign: 'right', fontFamily: "'IBM Plex Mono', monospace" }}>{e.server_count}</td>
              <td style={{ textAlign: 'right', fontFamily: "'IBM Plex Mono', monospace" }}>{e.cpu_total}</td>
              <td style={{ textAlign: 'right' }}>
                <span style={{
                  fontFamily: "'IBM Plex Mono', monospace",
                  color: utilColor(e.cpu_avg_pct), fontWeight: 600,
                }}>{e.cpu_avg_pct}%</span>
              </td>
              <td style={{ textAlign: 'right', fontFamily: "'IBM Plex Mono', monospace" }}>{fmtSize(e.ram_total_gb)}</td>
              <td style={{ textAlign: 'right' }}>
                <span style={{
                  fontFamily: "'IBM Plex Mono', monospace",
                  color: utilColor(e.ram_avg_pct), fontWeight: 600,
                }}>{e.ram_avg_pct}%</span>
              </td>
              <td style={{ textAlign: 'right', fontFamily: "'IBM Plex Mono', monospace" }}>{fmtSize(e.disk_total_gb)}</td>
              <td style={{ textAlign: 'right', fontFamily: "'IBM Plex Mono', monospace" }}>{fmtSize(e.disk_used_gb)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}


/* ══════════════════════════════════════════════════════════════════════════
   ACTION PANEL — grouped by action type
══════════════════════════════════════════════════════════════════════════ */
function ActionPanel({ actions, onServerClick }) {
  if (!actions) return null

  const sections = [
    {
      key: 'disk_critical',
      icon: '⬤',
      iconColor: 'var(--critical)',
      title: 'Disk Running Out',
      subtitle: 'Storage at risk — expand or clean up',
      items: actions.disk_critical || [],
      columns: (s) => (
        <>
          <td style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11 }}>
            {fmtSize(s.disk_free_gb)} free / {fmtSize(s.disk_total_gb)}
          </td>
          <td style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11 }}>
            {s.db_growth_gb_day > 0 ? `+${s.db_growth_gb_day.toFixed(1)} GB/day` : '—'}
          </td>
          <td style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, fontWeight: 600, color: s.runway_days < 30 ? 'var(--critical)' : s.runway_days < 60 ? 'var(--warning)' : 'var(--text-secondary)' }}>
            {s.runway_days < 9999 ? `${s.runway_days}d` : '—'}
          </td>
        </>
      ),
      headers: ['Free / Total', 'DB Growth', 'Full In'],
    },
    {
      key: 'overloaded',
      icon: '⬤',
      iconColor: 'var(--warning)',
      title: 'Overloaded',
      subtitle: 'CPU or RAM above safe threshold — scale up or optimise',
      items: actions.overloaded || [],
      columns: (s) => (
        <>
          <td>
            <span style={{
              fontFamily: "'IBM Plex Mono', monospace", fontSize: 11,
              fontWeight: 600, color: s.cpu_load_pct > 75 ? 'var(--critical)' : 'var(--text-secondary)',
            }}>CPU {s.cpu_load_pct}%</span>
            <span style={{ color: 'var(--text-muted)', fontSize: 10 }}> / {s.cpu_count} vCPU</span>
          </td>
          <td>
            <span style={{
              fontFamily: "'IBM Plex Mono', monospace", fontSize: 11,
              fontWeight: 600, color: s.ram_load_pct > 80 ? 'var(--critical)' : 'var(--text-secondary)',
            }}>RAM {s.ram_load_pct}%</span>
            <span style={{ color: 'var(--text-muted)', fontSize: 10 }}> / {s.ram_gb} GB</span>
          </td>
          <td>
            <span className={`badge ${s.bottleneck === 'CPU + RAM' ? 'badge-urgent' : s.bottleneck === 'CPU' ? 'badge-high' : 'badge-medium'}`} style={{ fontSize: 9 }}>
              {s.bottleneck}
            </span>
          </td>
        </>
      ),
      headers: ['CPU', 'RAM', 'Bottleneck'],
    },
    {
      key: 'underutilised',
      icon: '⬤',
      iconColor: 'var(--info)',
      title: 'Underutilised',
      subtitle: 'High provisioned resources, low usage — consider scaling down',
      items: actions.underutilised || [],
      columns: (s) => (
        <>
          <td style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11 }}>
            {s.cpu_load_pct}%
            <span style={{ color: 'var(--text-muted)' }}> on {s.cpu_count} vCPU</span>
          </td>
          <td style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11 }}>
            {s.ram_load_pct > 0 ? `${s.ram_load_pct}%` : '—'}
            <span style={{ color: 'var(--text-muted)' }}> on {s.ram_gb} GB</span>
          </td>
          <td style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: 'var(--text-muted)' }}>
            {s.total_alerts} alerts
          </td>
        </>
      ),
      headers: ['CPU Load', 'RAM Load', 'Alerts'],
    },
    {
      key: 'fast_growing_db',
      icon: '⬤',
      iconColor: 'var(--amber)',
      title: 'Fast-Growing Databases',
      subtitle: 'Database growth >500 MB/day — monitor storage runway',
      items: actions.fast_growing_db || [],
      columns: (s) => (
        <>
          <td style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11 }}>
            {s.db_count} DBs, {fmtSize(s.db_total_size_gb)}
          </td>
          <td style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, fontWeight: 600, color: 'var(--warning)' }}>
            +{s.db_growth_gb_day.toFixed(1)} GB/day
          </td>
          <td style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11 }}>
            {fmtSize(s.disk_free_gb)} free → {s.runway_days < 9999 ? `${s.runway_days}d` : '—'}
          </td>
        </>
      ),
      headers: ['Databases', 'Growth', 'Free → Runway'],
    },
    {
      key: 'alert_storms',
      icon: '⬤',
      iconColor: 'var(--text-muted)',
      title: 'Alert Storms',
      subtitle: 'Many alerts with low load — likely noisy triggers, tune thresholds',
      items: actions.alert_storms || [],
      columns: (s) => (
        <>
          <td style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, fontWeight: 600 }}>
            {s.total_alerts} alerts
          </td>
          <td style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: 'var(--text-muted)' }}>
            {s.cpu_load_pct}% CPU
          </td>
          <td style={{ fontSize: 11, color: 'var(--text-muted)' }}>
            Tune thresholds
          </td>
        </>
      ),
      headers: ['Alerts', 'CPU', 'Suggested'],
    },
  ]

  const activeSections = sections.filter(s => s.items.length > 0)
  if (activeSections.length === 0) {
    return (
      <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>
        <div style={{ fontSize: 28, marginBottom: 8, opacity: 0.4 }}>✓</div>
        <div style={{ fontWeight: 600, color: 'var(--success)' }}>No action items</div>
        <div style={{ fontSize: 12, marginTop: 4 }}>All servers are within healthy thresholds</div>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {activeSections.map(section => (
        <div key={section.key} className="card" style={{ padding: 0, overflow: 'hidden' }}>
          <div style={{
            padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 10,
            borderBottom: '0.5px solid var(--border)',
          }}>
            <span style={{ color: section.iconColor, fontSize: 8 }}>{section.icon}</span>
            <div style={{ flex: 1 }}>
              <span style={{ fontWeight: 700, fontSize: 13 }}>{section.title}</span>
              <span style={{
                fontFamily: "'IBM Plex Mono', monospace", fontSize: 10,
                color: 'var(--text-muted)', marginLeft: 8,
              }}>
                {section.items.length} server{section.items.length !== 1 ? 's' : ''}
              </span>
            </div>
            <div style={{
              fontFamily: "'IBM Plex Mono', monospace", fontSize: 9,
              color: 'var(--text-muted)', maxWidth: 280, textAlign: 'right',
            }}>
              {section.subtitle}
            </div>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table className="data-table" style={{ fontSize: 12 }}>
              <thead>
                <tr>
                  <th>Server</th>
                  <th>Env</th>
                  <th>Priority</th>
                  {section.headers.map(h => <th key={h}>{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {section.items.map(s => (
                  <tr key={s.name} style={{ cursor: 'pointer' }} onClick={() => onServerClick(s.name)}>
                    <td style={{ fontWeight: 600, fontSize: 12 }}>{s.name}</td>
                    <td style={{ fontSize: 11, color: 'var(--text-muted)' }}>{s.environment}</td>
                    <td>{priorityBadge(s.priority)}</td>
                    {section.columns(s)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  )
}


/* ══════════════════════════════════════════════════════════════════════════
   SERVER TABLE — the main resource breakdown table
══════════════════════════════════════════════════════════════════════════ */
function ResourceTable({ servers, onServerClick }) {
  const [sortCol, setSortCol] = useState('priority')
  const [sortAsc, setSortAsc] = useState(true)

  const toggleSort = (col) => {
    if (sortCol === col) {
      setSortAsc(!sortAsc)
    } else {
      setSortCol(col)
      setSortAsc(col === 'name' || col === 'priority')
    }
  }

  const PRIORITY_ORDER = { URGENT: 0, HIGH: 1, MEDIUM: 2, LOW: 3, NONE: 4 }

  const sorted = useMemo(() => {
    if (!servers) return []
    const s = [...servers]
    const dir = sortAsc ? 1 : -1
    s.sort((a, b) => {
      let va, vb
      switch (sortCol) {
        case 'name': va = a.name.toLowerCase(); vb = b.name.toLowerCase(); return va < vb ? -dir : va > vb ? dir : 0
        case 'priority': va = PRIORITY_ORDER[a.priority] ?? 5; vb = PRIORITY_ORDER[b.priority] ?? 5; return (va - vb) * dir
        case 'cpu': return (b.cpu_load_pct - a.cpu_load_pct) * dir
        case 'ram': return (b.ram_load_pct - a.ram_load_pct) * dir
        case 'disk': return (b.disk_max_util_pct - a.disk_max_util_pct) * dir
        case 'db_growth': return (b.db_growth_gb_day - a.db_growth_gb_day) * dir
        case 'runway': return (a.storage_runway_days - b.storage_runway_days) * dir
        case 'alerts': return (b.total_alerts - a.total_alerts) * dir
        default: return 0
      }
    })
    return s
  }, [servers, sortCol, sortAsc])

  const sortIcon = (col) => {
    if (sortCol !== col) return ''
    return sortAsc ? ' ↑' : ' ↓'
  }

  const thStyle = { cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap' }

  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ overflowX: 'auto', maxHeight: 520 }}>
        <table className="data-table" style={{ fontSize: 12 }}>
          <thead>
            <tr>
              <th style={thStyle} onClick={() => toggleSort('name')}>Server{sortIcon('name')}</th>
              <th>Env</th>
              <th style={thStyle} onClick={() => toggleSort('priority')}>Priority{sortIcon('priority')}</th>
              <th style={{ ...thStyle, textAlign: 'center' }} onClick={() => toggleSort('cpu')}>CPU{sortIcon('cpu')}</th>
              <th style={{ ...thStyle, textAlign: 'center' }} onClick={() => toggleSort('ram')}>RAM{sortIcon('ram')}</th>
              <th style={{ ...thStyle, textAlign: 'center' }} onClick={() => toggleSort('disk')}>Disk{sortIcon('disk')}</th>
              <th style={{ ...thStyle, textAlign: 'center' }} onClick={() => toggleSort('db_growth')}>DB Growth{sortIcon('db_growth')}</th>
              <th style={{ ...thStyle, textAlign: 'center' }} onClick={() => toggleSort('runway')}>Runway{sortIcon('runway')}</th>
              <th style={{ ...thStyle, textAlign: 'center' }} onClick={() => toggleSort('alerts')}>Alerts{sortIcon('alerts')}</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map(s => (
              <tr key={s.name} style={{ cursor: 'pointer' }} onClick={() => onServerClick(s.name)}>
                <td style={{ fontWeight: 600, fontSize: 12, maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {s.name}
                </td>
                <td style={{ fontSize: 10, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>{s.environment}</td>
                <td>{priorityBadge(s.priority)}</td>
                <td style={{ textAlign: 'center' }}>
                  <MiniBar used={s.cpu_used} total={s.cpu_count} unit="vCPU" pct={s.cpu_load_pct} width={64} />
                </td>
                <td style={{ textAlign: 'center' }}>
                  <MiniBar used={s.ram_used_gb} total={s.ram_gb} unit="GB" pct={s.ram_load_pct} width={64} />
                </td>
                <td style={{ textAlign: 'center' }}>
                  <MiniBar used={s.disk_used_gb} total={s.disk_total_gb} pct={s.disk_max_util_pct} width={72} />
                </td>
                <td style={{ textAlign: 'center', fontFamily: "'IBM Plex Mono', monospace", fontSize: 11 }}>
                  {s.db_growth_gb_day > 0.001
                    ? <span style={{ color: s.db_growth_gb_day > 1 ? 'var(--warning)' : 'var(--text-secondary)' }}>
                        +{s.db_growth_gb_day >= 1 ? s.db_growth_gb_day.toFixed(1) : (s.db_growth_gb_day * 1024).toFixed(0) + ' MB'}/d
                      </span>
                    : <span style={{ color: 'var(--text-muted)' }}>—</span>
                  }
                  {s.db_count > 0 && (
                    <div style={{ fontSize: 9, color: 'var(--text-muted)' }}>{s.db_count} DB{s.db_count > 1 ? 's' : ''}</div>
                  )}
                </td>
                <td style={{
                  textAlign: 'center', fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, fontWeight: 600,
                  color: s.storage_runway_days < 30 ? 'var(--critical)' : s.storage_runway_days < 90 ? 'var(--warning)' : 'var(--text-muted)',
                }}>
                  {fmtDays(s.storage_runway_days)}
                </td>
                <td style={{
                  textAlign: 'center', fontFamily: "'IBM Plex Mono', monospace", fontSize: 11,
                  fontWeight: s.total_alerts > 10 ? 600 : 400,
                  color: s.total_alerts > 10 ? 'var(--warning)' : 'var(--text-muted)',
                }}>
                  {s.total_alerts || '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sorted.length > 0 && (
        <div style={{
          padding: '8px 16px', borderTop: '0.5px solid var(--border)',
          fontFamily: "'IBM Plex Mono', monospace", fontSize: 9, color: 'var(--text-muted)',
        }}>
          {sorted.length} server{sorted.length !== 1 ? 's' : ''} • Click any row to drill down
        </div>
      )}
    </div>
  )
}


/* ══════════════════════════════════════════════════════════════════════════
   MAIN PAGE
══════════════════════════════════════════════════════════════════════════ */
export default function Resources({ filters }) {
  const { data: servers, isLoading: serversLoading } = useResourceServers(filters)
  const { data: fleet } = useFleetSummary(filters)
  const { data: actions } = useActionItems(filters)

  const [drillServer, setDrillServer] = useState(null)
  const [activeView, setActiveView] = useState('table')   // 'table' | 'actions'

  const openDrill = (name) => setDrillServer({ name, triageStatus: 'Open' })
  const closeDrill = () => setDrillServer(null)

  // Count total action items
  const actionCount = actions
    ? (actions.disk_critical?.length || 0) + (actions.overloaded?.length || 0) +
      (actions.underutilised?.length || 0) + (actions.fast_growing_db?.length || 0) +
      (actions.alert_storms?.length || 0)
    : 0

  return (
    <div>
      {/* Fleet summary */}
      <FleetBar fleet={fleet} />

      {/* Environment comparison */}
      {fleet?.by_environment?.length >= 2 && (
        <EnvComparison envData={fleet.by_environment} />
      )}

      {/* View toggle */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        marginBottom: 12,
      }}>
        <div style={{ display: 'flex', gap: 2, background: 'rgba(28,20,16,0.06)', borderRadius: 8, padding: 2 }}>
          <button
            onClick={() => setActiveView('table')}
            style={{
              padding: '6px 16px', borderRadius: 6, border: 'none', cursor: 'pointer',
              fontFamily: "'DM Sans', sans-serif", fontSize: 12, fontWeight: 600,
              background: activeView === 'table' ? 'var(--cream-card)' : 'transparent',
              color: activeView === 'table' ? 'var(--text-primary)' : 'var(--text-muted)',
              boxShadow: activeView === 'table' ? 'var(--shadow-sm)' : 'none',
              transition: 'all 0.15s',
            }}
          >
            All Servers
          </button>
          <button
            onClick={() => setActiveView('actions')}
            style={{
              padding: '6px 16px', borderRadius: 6, border: 'none', cursor: 'pointer',
              fontFamily: "'DM Sans', sans-serif", fontSize: 12, fontWeight: 600,
              background: activeView === 'actions' ? 'var(--cream-card)' : 'transparent',
              color: activeView === 'actions' ? 'var(--text-primary)' : 'var(--text-muted)',
              boxShadow: activeView === 'actions' ? 'var(--shadow-sm)' : 'none',
              transition: 'all 0.15s',
              display: 'flex', alignItems: 'center', gap: 6,
            }}
          >
            Act on This
            {actionCount > 0 && (
              <span style={{
                background: 'var(--critical)', color: '#fff',
                fontSize: 9, fontWeight: 700, padding: '1px 6px',
                borderRadius: 10, fontFamily: "'IBM Plex Mono', monospace",
              }}>
                {actionCount}
              </span>
            )}
          </button>
        </div>

        <div style={{
          fontFamily: "'IBM Plex Mono', monospace", fontSize: 9,
          color: 'var(--text-muted)', letterSpacing: '0.04em',
        }}>
          RESOURCE OVERVIEW
        </div>
      </div>

      {/* Content */}
      {serversLoading ? (
        <div className="card" style={{ height: 300, position: 'relative', overflow: 'hidden' }}>
          <div style={{
            position: 'absolute', inset: 0,
            background: 'linear-gradient(90deg, var(--bg-canvas) 25%, var(--bg-card) 50%, var(--bg-canvas) 75%)',
            backgroundSize: '200% 100%', animation: 'shimmer 1.5s infinite',
          }} />
        </div>
      ) : activeView === 'table' ? (
        <ResourceTable servers={servers} onServerClick={openDrill} />
      ) : (
        <ActionPanel actions={actions} onServerClick={openDrill} />
      )}

      {/* DrillDown slide-over */}
      {drillServer && (
        <DrillDown
          serverName={drillServer.name}
          triageStatus={drillServer.triageStatus}
          onClose={closeDrill}
        />
      )}
    </div>
  )
}
