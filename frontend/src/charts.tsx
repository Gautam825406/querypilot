import React from 'react'

function fmt(v: any): string {
  if (typeof v === 'number') return v >= 100 ? `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : `${v}`
  return String(v)
}

export function Chart({ spec }: { spec: any }) {
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
  const yKey = Object.keys(values[0])[values[0] ? Object.keys(values[0]).length - 1 : 1]
  const nums = values.map((v) => Number(v[yKey]) || 0)
  const max = Math.max(...nums, 1)
  const W = 640, H = 240, pad = 36
  if (spec.type === 'line') {
    const pts = values.map((v, i) => {
      const x = pad + (i * (W - pad * 2)) / Math.max(values.length - 1, 1)
      const y = H - pad - (nums[i] / max) * (H - pad * 2)
      return `${x},${y}`
    })
    return (
      <svg viewBox={`0 0 ${W} ${H}`} className="chart">
        <polyline points={pts.join(' ')} fill="none" stroke="#4f46e5" strokeWidth="3" />
        {values.map((v, i) => {
          const x = pad + (i * (W - pad * 2)) / Math.max(values.length - 1, 1)
          const y = H - pad - (nums[i] / max) * (H - pad * 2)
          return <circle key={i} cx={x} cy={y} r="4" fill="#4f46e5"><title>{`${v[xKey]}: ${fmt(v[yKey])}`}</title></circle>
        })}
        <text x={pad} y={H - 8} fontSize="12" fill="#64748b">{String(values[0][xKey]).slice(0, 10)}</text>
        <text x={W - pad} y={H - 8} fontSize="12" fill="#64748b" textAnchor="end">{String(values[values.length - 1][xKey]).slice(0, 10)}</text>
      </svg>
    )
  }
  // bar
  const bw = (W - pad * 2) / values.length
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart">
      {values.map((v, i) => {
        const h = (nums[i] / max) * (H - pad * 2)
        return (
          <g key={i}>
            <rect x={pad + i * bw + 6} y={H - pad - h} width={Math.max(bw - 12, 4)} height={h} rx="4" fill="#4f46e5">
              <title>{`${v[xKey]}: ${fmt(v[yKey])}`}</title>
            </rect>
            <text x={pad + i * bw + bw / 2} y={H - 8} fontSize="11" fill="#64748b" textAnchor="middle">
              {String(v[xKey]).slice(0, 10)}
            </text>
          </g>
        )
      })}
    </svg>
  )
}
