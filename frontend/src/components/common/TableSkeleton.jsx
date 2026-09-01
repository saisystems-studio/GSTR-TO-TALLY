export default function TableSkeleton({ rows = 5, cols = 6 }) {
  return <div className="table-skeleton" aria-hidden="true">
    {Array.from({ length: rows }, (_, r) => <div className="skeleton-row" key={r}>
      {Array.from({ length: cols }, (_, c) => <span className="skeleton-cell" key={c} style={{ width: `${55 + ((r * 7 + c * 13) % 40)}%` }} />)}
    </div>)}
  </div>
}
