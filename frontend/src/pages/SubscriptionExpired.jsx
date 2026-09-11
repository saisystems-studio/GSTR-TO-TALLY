import { formatSubscriptionDate } from '../components/common/SubscriptionBanner.jsx'

export default function SubscriptionExpired({ user, subscription, onLogout }) {
  const expiry = formatSubscriptionDate(subscription?.expiry_date)
  return <div className="subscription-expired-screen">
    <div className="subscription-expired-card">
      <span className="subscription-expired-icon" aria-hidden="true">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9" /><path d="M12 8v5" /><path d="M12 16h.01" /></svg>
      </span>
      <h1>GSTR 2 Tally License Expired</h1>
      {expiry && <p className="subscription-expired-date">Your subscription expired on: <b>{expiry}</b></p>}
      <p className="subscription-expired-copy">To continue using GSTR 2 Tally, renew your subscription.</p>
      <a className="subscription-expired-renew" href="mailto:support@example.com?subject=Renew%20GSTR%202%20Tally%20subscription">Renew Subscription</a>

      <div className="subscription-expired-details">
        <h2>Subscription Details</h2>
        <dl>
          <div><dt>Plan</dt><dd>{subscription?.plan || '-'}</dd></div>
          <div><dt>Activated On</dt><dd>{formatSubscriptionDate(subscription?.activation_date) || '-'}</dd></div>
          <div><dt>Expired On</dt><dd>{expiry || '-'}</dd></div>
          <div><dt>Status</dt><dd className="is-expired">Expired</dd></div>
        </dl>
      </div>

      <button type="button" className="subscription-expired-logout" onClick={onLogout}>Logout{user?.username ? ` (${user.username})` : ''}</button>
    </div>
  </div>
}
