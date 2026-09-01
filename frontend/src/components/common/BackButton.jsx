export default function BackButton({ onClick }) {
  return <button type="button" className="nav-back" onClick={onClick} aria-label="Go back">
    <svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M10 3 5 8l5 5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
  </button>
}
