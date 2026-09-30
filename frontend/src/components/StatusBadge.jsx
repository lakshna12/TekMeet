import React from 'react';

const StatusBadge = ({ status }) => {
  const normalizedStatus = (status || 'unknown').toLowerCase();
  
  let badgeClass = 'badge-scheduled';
  let label = status || 'Scheduled';

  if (normalizedStatus.includes('completed') || normalizedStatus.includes('success') || normalizedStatus.includes('delivered')) {
    badgeClass = 'badge-completed';
  } else if (normalizedStatus.includes('joined') || normalizedStatus.includes('in_progress') || normalizedStatus.includes('processing')) {
    badgeClass = 'badge-joined';
  } else if (normalizedStatus.includes('fail') || normalizedStatus.includes('error')) {
    badgeClass = 'badge-failed';
  }

  return (
    <span className={`badge ${badgeClass}`}>
      {label}
    </span>
  );
};

export default StatusBadge;
