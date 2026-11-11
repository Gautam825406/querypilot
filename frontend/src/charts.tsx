import React, { useId } from 'react'

function fmt(v: any): string {
  if (typeof v === 'number') return v >= 100 ? `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : `${v}`
  return String(v)
}

function fmtAxis(v: number): string {
  if (v >= 1000) return `$${(v / 1000).toFixed(v % 1000 === 0 ? 0 : 1)}k`
  if (v === 0) return '$0'
  return `$${v}`
}

/** Catmull-Rom -> cubic bezier smooth path through points. */
function smoothPath(pts: { x: number; y: number }[]): string {
  if (pts.length < 3) return `M ${pts.map((p) => `${p.x},${p.y}`).join(' L ')}`
  let d = `M ${pts[0].x},${pts[0].y}`
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(i - 1, 0)]
    const p1 = pts[i]
    const p2 = pts[i + 1]
    const p3 = pts[Math.min(i + 2, pts.length - 1)]
    const c1x = p1.x + (p2.x - p0.x) / 6
    const c1y = p1.y + (p2.y - p0.y) / 6
    const c2x = p2.x - (p3.x - p1.x) / 6
    const c2y = p2.y - (p3.y - p1.y) / 6
    d += ` C ${c1x.toFixed(1)},${c1y.toFixed(1)} ${c2x.toFixed(1)},${c2y.toFixed(1)} ${p2.x},${p2.y}`
  }
  return d
}

export function Chart({ spec }: { spec: any }) {
  const gid = useId().replace(/[^a-zA-Z0-9]/g, '')
  if (!spec || spec.type === 'none' || !spec.data?.values?.length) return null
  const values = spec.data.values as any[]
  if (spec.type === 'stat') {
    const cols = Object.keys(values[0])
    return (
      <div className="stat">
        <div className="stat-label">{cols[cols.length - 1].replace(/_/g, ' ')}</div>
        <div className="stat-value">{fmt(values[0][cols[cols.length - 1]])}</div>
      </div>
    )
  }
  const xKey = Object.keys(values[0])[0]
  const yKey = Object.keys(values[0])[Object.keys(values[0]).length - 1]
  const nums = values.map((v) => Number(v[yKey]) || 0)
  const max = Math.max(...nums, 1)
  const W = 640, H = 260, padL = 52, padR = 16, padT = 26, padB = 34
  const yOf = (n: number) => H - padB - (n / max) * (H - padT - padB)
  const gridVals = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max)

  if (spec.type === 'line') {
    const pts = values.map((v, i) => ({
      x: padL + (i * (W - padL - padR)) / Math.max(values.length - 1, 1),
      y: yOf(nums[i]),
    }))
    const line = smoothPath(pts)
    const area = `${line} L ${pts[pts.length - 1].x},${H - padB} L ${pts[0].x},${H - padB} Z`
    return (
      <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="Line chart">
        <defs>
          <linearGradient id={`area-${gid}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#8b5cf6" stopOpacity="0.45" />
            <stop offset="100%" stopColor="#22d3ee" stopOpacity="0.02" />
          </linearGradient>
          <linearGradient id={`line-${gid}`} x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#a78bfa" />
            <stop offset="100%" stopColor="#22d3ee" />
          </linearGradient>
        </defs>
        {gridVals.map((g) => (
          <g key={g}>
            <line x1={padL} x2={W - padR} y1={yOf(g)} y2={yOf(g)} stroke="rgba(255,255,255,0.08)" strokeWidth="1" />
            <text x={padL - 8} y={yOf(g) + 4} fontSize="11" fill="#93a1c0" textAnchor="end">{fmtAxis(g)}</text>
          </g>
        ))}
        <path d={area} fill={`url(#area-${gid})`} />
        <path d={line} fill="none" stroke={`url(#line-${gid})`} strokeWidth="3" strokeLinecap="round" />
        {values.map((v, i) => (
          <g key={i}>
            <circle cx={pts[i].x} cy={pts[i].y} r="8" fill="#8b5cf6" opacity="0.25" />
            <circle cx={pts[i].x} cy={pts[i].y} r="4" fill="#0a1020" stroke="#a78bfa" strokeWidth="2">
              <title>{`${v[xKey]}: ${fmt(v[yKey])}`}</title>
            </circle>
            <text x={pts[i].x} y={pts[i].y - 13} fontSize="11" fill="#c7d2ea" textAnchor="middle" fontWeight="600">
              {fmt(v[yKey])}
            </text>
          </g>
        ))}
        <text x={padL} y={H - 10} fontSize="12" fill="#93a1c0">{String(values[0][xKey]).slice(0, 10)}</text>
        <text x={W - padR} y={H - 10} fontSize="12" fill="#93a1c0" textAnchor="end">{String(values[values.length - 1][xKey]).slice(0, 10)}</text>
      </svg>
    )
  }
  // bar
  const bw = (W - padL - padR) / values.length
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="Bar chart">
      <defs>
        <linearGradient id={`bar-${gid}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#a78bfa" />
          <stop offset="100%" stopColor="#22d3ee" />
        </linearGradient>
      </defs>
      {gridVals.map((g) => (
        <g key={g}>
          <line x1={padL} x2={W - padR} y1={yOf(g)} y2={yOf(g)} stroke="rgba(255,255,255,0.08)" strokeWidth="1" />
          <text x={padL - 8} y={yOf(g) + 4} fontSize="11" fill="#93a1c0" textAnchor="end">{fmtAxis(g)}</text>
        </g>
      ))}
      {values.map((v, i) => {
        const h = Math.max((nums[i] / max) * (H - padT - padB), nums[i] > 0 ? 3 : 0)
        const x = padL + i * bw + Math.max(bw * 0.14, 4)
        const w = Math.max(bw * 0.72, 4)
        return (
          <g key={i}>
            <rect x={x} y={H - padB - h} width={w} height={h} rx="6" fill={`url(#bar-${gid})`}>
              <title>{`${v[xKey]}: ${fmt(v[yKey])}`}</title>
            </rect>
            <text x={x + w / 2} y={H - padB - h - 7} fontSize="11" fill="#c7d2ea" textAnchor="middle" fontWeight="600">
              {fmt(v[yKey])}
            </text>
            <text x={padL + i * bw + bw / 2} y={H - 10} fontSize="11" fill="#93a1c0" textAnchor="middle">
              {String(v[xKey]).slice(0, 12)}
            </text>
          </g>
        )
      })}
    </svg>
  )
}
