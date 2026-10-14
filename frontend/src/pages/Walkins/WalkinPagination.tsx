export default function WalkinPagination({ page, total, pageSize = 100, loading, onPage }: { page: number; total: number; pageSize?: number; loading: boolean; onPage(page: number): void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  return <div className="walkin-header-actions" aria-label="Visit pagination">
    <span>{total} results · Page {page} of {pages}</span>
    <button className="walkin-button secondary" type="button" disabled={loading || page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
    <button className="walkin-button secondary" type="button" disabled={loading || page >= pages} onClick={() => onPage(page + 1)}>Next</button>
  </div>
}
