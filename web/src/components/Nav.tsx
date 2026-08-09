import { NavLink } from 'react-router-dom'
import type { ActionSpec } from '../api/types'

export function Nav({ actions }: { actions: ActionSpec[] }) {
  return (
    <nav className="nav">
      <h1>Photo Library</h1>
      <section>
        <h2>Setup</h2>
        <NavLink to="/settings">Settings</NavLink>
      </section>
      <section>
        <h2>Actions</h2>
        {actions.map((action) => (
          <NavLink key={action.id} to={`/actions/${action.id}`}>
            {action.title}
          </NavLink>
        ))}
      </section>
      <section>
        <h2>Activity</h2>
        <NavLink to="/jobs">Jobs</NavLink>
      </section>
    </nav>
  )
}
