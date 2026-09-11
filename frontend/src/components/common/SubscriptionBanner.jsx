const formatDate = value => {
  if (!value) return ''
  const date = new Date(`${value}T00:00:00`)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })
}

// Compact amber "expiring soon" strip (spec section 21) -- never shown for
// ACTIVE/TRIAL, never red (that's the full expired screen, not this
// banner). Renders nothing at all unless the backend actually says
// EXPIRING_SOON, so it can never fabricate urgency.
export default function SubscriptionBanner({ subscription }) {
  if (!subscription || subscription.subscription_status !== 'EXPIRING_SOON') return null
  const days = subscription.days_remaining
  return <div className="subscription-banner" role="status">
    <span className="subscription-banner-text">
      Subscription expires in {days} {days === 1 ? 'day' : 'days'}
      <span className="subscription-banner-date">{formatDate(subscription.expiry_date)}</span>
    </span>
    <a className="subscription-banner-action" href="mailto:support@example.com?subject=Renew%20GSTR%202%20Tally%20subscription">Renew</a>
  </div>
}

// Small header/profile readout (spec section 20) -- deliberately just two
// lines of text, no icons/colors, so it stays visually quiet on every
// screen. Omitted entirely once expired (the dedicated expired screen owns
// that message instead).
export function SubscriptionCompactInfo({ subscription }) {
  if (!subscription || !subscription.is_activated || subscription.subscription_status === 'EXPIRED') return null
  return <div className="subscription-compact-info">
    <span>{subscription.plan}</span>
    <span>Expires {formatDate(subscription.expiry_date)}</span>
  </div>
}

export { formatDate as formatSubscriptionDate }
