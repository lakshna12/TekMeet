import React from 'react';

const Footer = () => {
  return (
    <footer className="footer">
      <div className="nav-content" style={{ justifyContent: 'center' }}>
        <p>
          TekMeet &copy; {new Date().getFullYear()} — Autonomous Teams AI Meeting Assistant & Multi-Engine Summarizer
        </p>
      </div>
    </footer>
  );
};

export default Footer;
