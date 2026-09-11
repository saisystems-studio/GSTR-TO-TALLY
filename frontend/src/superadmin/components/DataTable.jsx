import { useEffect, useState } from 'react'

/** Generic paginated/searchable/filterable table (spec sections 56/57) --
 * shared by every list page so pagination/search debouncing is implemented
 * once. `fetcher(params)` must return `{ results, total, page, num_pages }`. */
export default function DataTable({ columns, fetcher, filters = [], searchable = true, reloadToken = 0, emptyLabel = 'No records found.' }) {
  const [search, setSearch] = useState('')
  const [filterValues, setFilterValues] = useState({})
  const [page, setPage] = useState(1)
  const [state, setState] = useState({ loading: true, results: [], total: 0, numPages: 1, error: '' })

  useEffect(() => {
    let cancelled = false
    setState(s => ({ ...s, loading: true }))
    const timer = window.setTimeout(() => {
      fetcher({ search, page, ...filterValues }).then(data => {
        if (cancelled) return
        setState({ loading: false, results: data.results || [], total: data.total || 0, numPages: data.num_pages || 1, error: '' })
      }).catch(error => {
        if (cancelled) return
        setState({ loading: false, results: [], total: 0, numPages: 1, error: error.message || 'Failed to load.' })
      })
    }, search ? 300 : 0)
    return () => { cancelled = true; window.clearTimeout(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, page, JSON.stringify(filterValues), reloadToken])

  useEffect(() => { setPage(1) }, [search, JSON.stringify(filterValues)])

  return (
    <div>
      {(searchable || filters.length > 0) && (
        <div className="sa-table-toolbar">
          {searchable && (
            <input className="sa-input sa-search-input" placeholder="Search..." value={search}
                   onChange={e => setSearch(e.target.value)} />
          )}
          {filters.map(filter => (
            <select key={filter.key} className="sa-input" style={{ width: 'auto' }}
                    value={filterValues[filter.key] || ''}
                    onChange={e => setFilterValues(v => ({ ...v, [filter.key]: e.target.value }))}>
              <option value="">{filter.label}: All</option>
              {filter.options.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
            </select>
          ))}
        </div>
      )}
      <div className="sa-table-wrap">
        <table className="sa-table">
          <thead><tr>{columns.map(col => <th key={col.key}>{col.label}</th>)}</tr></thead>
          <tbody>
            {state.results.map((row, i) => (
              <tr key={row.id ?? row.customer_id ?? i}>
                {columns.map(col => <td key={col.key}>{col.render ? col.render(row) : (row[col.key] ?? '—')}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
        {!state.loading && state.results.length === 0 && !state.error && <div className="sa-table-empty">{emptyLabel}</div>}
        {state.loading && <div className="sa-table-empty">Loading...</div>}
        {state.error && <div className="sa-table-empty" style={{ color: 'var(--sa-error)' }}>{state.error}</div>}
      </div>
      {state.numPages > 1 && (
        <div className="sa-pagination">
          <button className="sa-action-link" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Prev</button>
          <span>Page {page} of {state.numPages} ({state.total} total)</span>
          <button className="sa-action-link" disabled={page >= state.numPages} onClick={() => setPage(p => p + 1)}>Next</button>
        </div>
      )}
    </div>
  )
}
