import React from 'react';
import { NavLink, Link } from 'react-router-dom';
import { Calendar, FileText, Bot, LogOut, LogIn, ShieldCheck } from 'lucide-react';
import { useAuth } from '../context/AuthContext';

const Navbar = () => {
  const { user, logout, isEntraConnected } = useAuth();

  return (
    <nav className="navbar">
      <div className="nav-content">
        <Link to="/meetings" className="brand">
          <div className="brand-icon">
            <Bot size={22} />
          </div>
          <div>
            Tek<span className="accent">Meet</span>
          </div>
        </Link>

        <ul className="nav-links">
          <li>
            <NavLink
              to="/meetings"
              className={({ isActive }) => (isActive ? 'nav-item active' : 'nav-item')}
            >
              <Calendar size={18} />
              <span>Meeting Setup</span>
            </NavLink>
          </li>
          <li>
            <NavLink
              to="/summary"
              className={({ isActive }) => (isActive ? 'nav-item active' : 'nav-item')}
            >
              <FileText size={18} />
              <span>Meeting Summaries</span>
            </NavLink>
          </li>
        </ul>

        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
          <div className="status-indicator" title={isEntraConnected ? "Entra ID Bot Authenticated" : "Entra ID Disconnected"}>
            <div className={`status-dot ${isEntraConnected ? '' : 'offline'}`} />
            <span>{isEntraConnected ? "Graph Bot Active" : "Bot Standby"}</span>
          </div>

          {user ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                {user.email || user.username}
              </span>
              <button
                onClick={logout}
                className="btn btn-secondary btn-sm"
                title="Sign Out"
              >
                <LogOut size={16} />
                <span>Logout</span>
              </button>
            </div>
          ) : (
            <Link to="/login" className="btn btn-primary btn-sm">
              <LogIn size={16} />
              <span>Login</span>
            </Link>
          )}
        </div>
      </div>
    </nav>
  );
};

export default Navbar;
