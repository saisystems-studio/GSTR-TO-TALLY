export const workflowSteps = [
  ['upload', 'Upload'],
  ['preview', 'Preview'],
  ['parties', 'Verify'],
  ['masters', 'Masters'],
  ['vouchers', 'Vouchers'],
  ['import', 'Import'],
]

export default function StepProgress({ current }) {
  const currentIndex = workflowSteps.findIndex(([key]) => key === current)
  return <nav className="step-progress" aria-label="Workflow progress">
    {workflowSteps.map(([key, label], index) => {
      const state = index < currentIndex ? 'Completed' : index === currentIndex ? 'In Progress' : 'Pending'
      return <div key={key} className={`progress-step ${index < currentIndex ? 'done' : ''} ${index === currentIndex ? 'active' : ''}`}>
        <span>{index < currentIndex ? '✓' : index + 1}</span><div><strong>{label}</strong><small>{state}</small></div>
      </div>
    })}
  </nav>
}
