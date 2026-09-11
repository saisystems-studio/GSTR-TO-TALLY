const DEFAULT_LOGO = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 52 52'%3E%3Crect width='52' height='52' rx='10' fill='%23E3F2FD'/%3E%3Cpath d='M10 15h32M10 26h32M10 37h32M20 9v34M33 9v34' stroke='%2342A5F5' stroke-width='1.5' opacity='.45'/%3E%3Ccircle cx='26' cy='26' r='15' fill='%231E88E5'/%3E%3Ctext x='26' y='30.5' text-anchor='middle' font-family='Arial,sans-serif' font-size='13' font-weight='700' fill='white'%3EG2%3C/text%3E%3C/svg%3E"

export default function AppBrand({ logoSrc = DEFAULT_LOGO }) {
  return <div className="brand">
    <div className="brand-logo">
      <img className="brand-logo-image" src={logoSrc} alt="GSTR 2 Tally" />
    </div>
    <div className="brand-content">
      <strong>GSTR 2 Tally</strong>
      <span>GST Return Data Processing</span>
    </div>
  </div>
}
