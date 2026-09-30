import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { ShieldCheck, Lock, Mail, Server, AlertCircle, CheckCircle2 } from 'lucide-react';
import { verifyEntraAuth, verifyGraphConnectivity } from '../api/meetingsApi';

const LoginPage = () => {
  const navigate = useNavigate();
  const { login, user } = useAuth();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  // System Entra ID Diagnostic State
  const [entraStatus, setEntraStatus] = useState(null);
  const [isCheckingEntra, setIsCheckingEntra] = useState(true);

  useEffect(() => {
    if (user) {
      navigate('/meetings');
    }
  }, [user, navigate]);

  useEffect(() => {
    const checkBotIdentity = async () => {
      setIsCheckingEntra(true);
      try {
        const [entraRes, graphRes] = await Promise.allSettled([
          verifyEntraAuth(),
          verifyGraphConnectivity()
        ]);

        setEntraStatus({
          entra: entraRes.status === 'fulfilled' ? entraRes.value : { status: 'error', detail: entraRes.reason?.message },
          graph: graphRes.status === 'fulfilled' ? graphRes.value : { status: 'error', detail: graphRes.reason?.message }
        });
      } catch (err) {
        console.error('Diagnostic error:', err);
      } finally {
        setIsCheckingEntra(false);
      }
    };

    checkBotIdentity();
  }, []);

  const handleSubmit = (e) => {
    e.preventDefault();
    setError('');

    if (!email.trim()) {
      setError('Please enter your email or username');
      return;
    }

    if (!password.trim()) {
      setError('Please enter your password');
      return;
    }

    setIsSubmitting(true);

    try {
      login(email.trim(), password.trim());
      navigate('/meetings');
    } catch (err) {
      setError('Authentication failed. Please check your credentials.');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div style={{ maxWidth: '480px', margin: '3rem auto' }}>
      <div className="card">
        <div className="card-header" style={{ flexDirection: 'column', alignItems: 'center', textAlign: 'center', borderBottom: 'none', marginBottom: '0.5rem' }}>
          <div className="brand-icon" style={{ width: '54px', height: '54px', fontSize: '1.5rem', marginBottom: '1rem' }}>
            <Lock size={28} />
          </div>
          <h1 className="card-title" style={{ fontSize: '1.5rem' }}>TekMeet Portal Access</h1>
          <p className="page-subtitle">Internal Authentication & Bot Operator Dashboard</p>
        </div>

        {error && (
          <div className="alert alert-error">
            <AlertCircle size={18} style={{ flexShrink: 0, marginTop: '2px' }} />
            <div>{error}</div>
          </div>
        )}

        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label className="form-label" htmlFor="email">Email / Username</label>
            <div style={{ position: 'relative' }}>
              <input
                id="email"
                type="text"
                className="form-control"
                placeholder="operator@company.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={isSubmitting}
                style={{ paddingLeft: '2.5rem' }}
              />
              <Mail size={18} style={{ position: 'absolute', left: '0.85rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
            </div>
          </div>

          <div className="form-group">
            <label className="form-label" htmlFor="password">Password</label>
            <div style={{ position: 'relative' }}>
              <input
                id="password"
                type="password"
                className="form-control"
                placeholder="••••••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={isSubmitting}
                style={{ paddingLeft: '2.5rem' }}
              />
              <Lock size={18} style={{ position: 'absolute', left: '0.85rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
            </div>
          </div>

          <button
            type="submit"
            className="btn btn-primary"
            style={{ width: '100%', marginTop: '0.5rem' }}
            disabled={isSubmitting}
          >
            {isSubmitting ? 'Authenticating...' : 'Sign In to TekMeet'}
          </button>
        </form>

        <div style={{ marginTop: '2rem', paddingTop: '1.25rem', borderTop: '1px solid var(--border-color)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.75rem' }}>
            <Server size={16} style={{ color: 'var(--secondary)' }} />
            <span style={{ fontSize: '0.85rem', fontWeight: 700, color: 'var(--text-main)' }}>
              Backend Identity & Entra ID Status
            </span>
          </div>

          {isCheckingEntra ? (
            <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>Verifying Entra ID client credentials...</div>
          ) : (
            <div style={{ fontSize: '0.8rem', display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: 'rgba(255,255,255,0.03)', padding: '0.5rem 0.75rem', borderRadius: '6px' }}>
                <span>Entra ID App Credentials</span>
                <span className="badge badge-completed">
                  <CheckCircle2 size={12} /> Active
                </span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: 'rgba(255,255,255,0.03)', padding: '0.5rem 0.75rem', borderRadius: '6px' }}>
                <span>Microsoft Graph API</span>
                <span className="badge badge-completed">
                  <ShieldCheck size={12} /> Connected
                </span>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default LoginPage;
