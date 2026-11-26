import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { signOut } from "../api/client";

export function Layout() {
  const navigate = useNavigate();

  async function onSignOut() {
    try {
      await signOut();
    } finally {
      navigate("/signin");
    }
  }

  return (
    <div className="app">
      <header className="app-header">
        <span className="brand">DevTeam AI</span>
        <nav aria-label="Main">
          <NavLink to="/projects">Projects</NavLink>
          <NavLink to="/activity">Activity</NavLink>
          <NavLink to="/sandbox">Sandbox</NavLink>
          <NavLink to="/settings">Settings</NavLink>
          <NavLink to="/" end>
            System status
          </NavLink>
        </nav>
        <button type="button" className="link-button" onClick={() => void onSignOut()}>
          Sign out
        </button>
      </header>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}
