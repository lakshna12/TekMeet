import React, { createContext, useContext, useState, useEffect } from 'react';
import { meetingsApi } from '../api/meetingsApi';

const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(() => {
    const saved = localStorage.getItem('tekmeet_user');
    return saved ? JSON.parse(saved) : null;
  });
  const [authStatus, setAuthStatus] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchAuthStatus();
  }, []);

  const fetchAuthStatus = async () => {
    try {
      const data = await meetingsApi.getAuthStatus();
      setAuthStatus(data);
    } catch (err) {
      console.error('Failed to fetch auth status:', err);
    } finally {
      setLoading(false);
    }
  };

  const login = (email, password) => {
    // Simple internal authentication wrapper
    const userData = {
      email,
      name: email.split('@')[0],
      loggedInAt: new Date().toISOString(),
    };
    setUser(userData);
    localStorage.setItem('tekmeet_user', JSON.stringify(userData));
    return true;
  };

  const logout = () => {
    setUser(null);
    localStorage.removeItem('tekmeet_user');
  };

  return (
    <AuthContext.Provider value={{ user, authStatus, loading, login, logout, fetchAuthStatus }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => useContext(AuthContext);
