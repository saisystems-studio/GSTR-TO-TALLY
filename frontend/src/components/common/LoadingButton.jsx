export default function LoadingButton({ loading, disabled, onClick, children, className = '', type = 'button' }) {
  return <button
    type={type}
    className={`primary-button ${className} ${loading ? 'is-loading' : ''}`.trim()}
    disabled={disabled || loading}
    onClick={onClick}
  >
    {loading && <span className="button-spinner" aria-hidden="true" />}
    <span>{children}</span>
  </button>
}
