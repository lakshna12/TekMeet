import React from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import Navbar from './components/Navbar';
import Footer from './components/Footer';
import LoginPage from './pages/LoginPage';
import MeetingSetupPage from './pages/MeetingSetupPage';
import PastMeetingsPage from './pages/PastMeetingsPage';
import SummaryDetailPage from './pages/SummaryDetailPage';
import { AuthProvider } from './context/AuthContext';

const App = () => {
  return (
    <AuthProvider>
      <div className="app-container">
        <Navbar />
        <main className="main-content">
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/" element={<Navigate to="/meetings" replace />} />
            <Route path="/meetings" element={<MeetingSetupPage />} />
            <Route path="/meetings/:eventId" element={<SummaryDetailPage />} />
            <Route path="/summary" element={<PastMeetingsPage />} />
            <Route path="/summary/:eventId" element={<SummaryDetailPage />} />
            <Route path="*" element={<Navigate to="/meetings" replace />} />
          </Routes>
        </main>
        <Footer />
      </div>
    </AuthProvider>
  );
};

export default App;
