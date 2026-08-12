import { NavLink } from 'react-router-dom'
import type { ActionSpec } from '../api/types'

export function Nav({ actions }: { actions: ActionSpec[] }) {
  const flows = actions.filter((a) => a.group === 'flow')
  const advanced = actions.filter((a) => a.group !== 'flow')

  return (
    <nav className="nav">
      <h1>Photo Library</h1>
      <section>
        <h2>Setup</h2>
        <NavLink to="/settings">Settings</NavLink>
      </section>
      <section>
        <h2>Flows</h2>
        {flows.map((action) => (
          <NavLink key={action.id} to={`/actions/${action.id}`}>
            {action.title}
          </NavLink>
        ))}
      </section>
      <section>
        <h2>Browse</h2>
        <NavLink to="/library">Library</NavLink>
        <NavLink to="/tags">Tags</NavLink>
      </section>
      <section>
        <h2>Activity</h2>
        <NavLink to="/review">Review Plan</NavLink>
        <NavLink to="/jobs">Jobs</NavLink>
      </section>
      <section>
        <details>
          <summary>Advanced</summary>
          {advanced.map((action) => (
            <NavLink key={action.id} to={`/actions/${action.id}`}>
              {action.title}
            </NavLink>
          ))}
        </details>
      </section>
    </nav>
  )
}
