import React, { useState, useEffect } from 'react';
import {
  Calendar, Clock, Mail, Link as LinkIcon, PlusCircle, RefreshCw,
  Play, Square, AlertCircle, CheckCircle2, Video, Server, ChevronRight, PhoneCall, Radio
} from 'lucide-react';
import StatusBadge from '../components/StatusBadge';
import {
  getUpcomingMeetings, scheduleMeeting, joinMeeting, getCallRecords, getSchedulerStatus,
  triggerPollNow, startScheduler, stopScheduler
} from '../api/meetingsApi';
import { Link } from 'react-router-dom';

const MeetingSetupPage = () => {
  // Mode: 'instant' | 'schedule'
  const [activeTab, setActiveTab] = useState('instant');

  // Form State
  const [meetingUrl, setMeetingUrl] = useState('');
  const [scheduledDate, setScheduledDate] = useState(new Date().toISOString().split('T')[0]);
  const [scheduledTime, setScheduledTime] = useState('10:00');
  const [organizerEmail, setOrganizerEmail] = useState('');
  const [subject, setSubject] = useState('');

  // Submit Feedback
  const [formError, setFormError] = useState('');
  const [formSuccess, setFormSuccess] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Upcoming Meetings & Active Calls State
  const [upcomingMeetings, setUpcomingMeetings] = useState([]);
  const [activeCalls, setActiveCalls] = useState([]);
  const [isLoadingMeetings, setIsLoadingMeetings] = useState(true);
  const [meetingsError, setMeetingsError] = useState('');

  // Scheduler Diagnostic State
  const [schedulerInfo, setSchedulerInfo] = useState(null);
  const [isPollingNow, setIsPollingNow] = useState(false);

  // Fetch upcoming meetings, active calls & scheduler status
  const loadData = async () => {
    setIsLoadingMeetings(true);
    setMeetingsError('');
    try {
      const [meetingsData, schedulerData, callsData] = await Promise.allSettled([
        getUpcomingMeetings(),
        getSchedulerStatus(),
        getCallRecords()
      ]);

      if (meetingsData.status === 'fulfilled') {
        const list = Array.isArray(meetingsData.value) 
          ? meetingsData.value 
          : meetingsData.value?.meetings || meetingsData.value?.items || [];
        setUpcomingMeetings(list);
      } else {
        setMeetingsError(meetingsData.reason?.message || 'Failed to fetch upcoming meetings');
      }

      if (schedulerData.status === 'fulfilled') {
        setSchedulerInfo(schedulerData.value);
      }

      if (callsData.status === 'fulfilled') {
        setActiveCalls(Array.isArray(callsData.value) ? callsData.value : []);
      }
    } catch (err) {
      console.error('Error loading meeting setup data:', err);
    } finally {
      setIsLoadingMeetings(false);
    }
  };

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 10000);
    return () => clearInterval(interval);
  }, []);

  const handleInstantJoinSubmit = async (e) => {
    e.preventDefault();
    setFormError('');
    setFormSuccess('');

    if (!meetingUrl.trim()) {
      setFormError('Please enter a valid Microsoft Teams Meeting Link');
      return;
    }

    setIsSubmitting(true);
    try {
      const res = await joinMeeting({
        join_url: meetingUrl.trim(),
        subject: subject.trim() || 'Teams Meeting Session'
      });
      setFormSuccess(`Bot dispatch initiated successfully! Call ID: ${res.call_id || 'Active'}. Connecting to Media Worker...`);
      setMeetingUrl('');
      setSubject('');
      loadData();
    } catch (err) {
      setFormError(err.response?.data?.detail || err.message || 'Failed to join meeting. Please verify the URL and backend connectivity.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleScheduleSubmit = async (e) => {
    e.preventDefault();
    setFormError('');
    setFormSuccess('');

    if (!meetingUrl.trim()) {
      setFormError('Please enter a valid Teams Meeting Link or Meeting ID');
      return;
    }
    if (!organizerEmail.trim()) {
      setFormError('Please enter the Organizer Email');
      return;
    }

    setIsSubmitting(true);
    try {
      const payload = {
        join_url: meetingUrl.trim(),
        scheduled_date: scheduledDate,
        scheduled_time: scheduledTime,
        organizer_email: organizerEmail.trim(),
        subject: subject.trim() || 'Scheduled Teams Meeting'
      };

      const res = await scheduleMeeting(payload);
      setFormSuccess(res?.message || 'Meeting scheduled successfully! Microsoft Graph Calendar Watcher is tracking this session.');
      setMeetingUrl('');
      setSubject('');
      loadData();
    } catch (err) {
      setFormError(err.response?.data?.detail || err.message || 'Failed to schedule meeting');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handlePollNow = async () => {
    setIsPollingNow(true);
    try {
      await triggerPollNow();
      await loadData();
    } catch (err) {
      console.error('Poll failed:', err);
    } finally {
      setIsPollingNow(false);
    }
  };

  const handleToggleScheduler = async (action) => {
    try {
      if (action === 'start') {
        await startScheduler();
      } else {
        await stopScheduler();
      }
      await loadData();
    } catch (err) {
      console.error('Scheduler toggle failed:', err);
    }
  };

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">Meeting Setup & Dashboard</h1>
        <p className="page-subtitle">
          Configure Teams meeting credentials for the autonomous bot and monitor upcoming calendar events.
        </p>
      </div>

      <div className="grid-main-sidebar">
        {/* Left Column: Form & Upcoming Meetings */}
        <div>
          {/* Meeting Form Card with Tabs */}
          <div className="card" style={{ marginBottom: '1.5rem' }}>
            <div className="card-header" style={{ borderBottom: '1px solid var(--border-color)', paddingBottom: '1rem', marginBottom: '1.25rem' }}>
              <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
                <button
                  type="button"
                  onClick={() => { setActiveTab('instant'); setFormError(''); setFormSuccess(''); }}
                  className={`btn ${activeTab === 'instant' ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                  style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}
                >
                  <Radio size={16} className={activeTab === 'instant' ? 'spin' : ''} />
                  <span>Instant Bot Join (Live Call)</span>
                </button>

                <button
                  type="button"
                  onClick={() => { setActiveTab('schedule'); setFormError(''); setFormSuccess(''); }}
                  className={`btn ${activeTab === 'schedule' ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                  style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}
                >
                  <Calendar size={16} />
                  <span>Schedule Meeting</span>
                </button>
              </div>

              <span className="badge badge-completed">
                <Server size={12} style={{ display: 'inline', marginRight: '4px' }} />
                Media Worker Ready
              </span>
            </div>

            {formError && (
              <div className="alert alert-error">
                <AlertCircle size={18} />
                <div>{formError}</div>
              </div>
            )}

            {formSuccess && (
              <div className="alert alert-success">
                <CheckCircle2 size={18} />
                <div>{formSuccess}</div>
              </div>
            )}

            {activeTab === 'instant' ? (
              /* Instant Join Live Meeting Form */
              <form onSubmit={handleInstantJoinSubmit}>
                <div style={{ marginBottom: '1rem', fontSize: '0.875rem', color: 'var(--text-muted)' }}>
                  Paste an active Microsoft Teams Meeting URL below to dispatch the <strong>TekMeet Notetaker Bot</strong> into the live call immediately.
                </div>

                <div className="form-group">
                  <label className="form-label" htmlFor="meeting-url">Teams Meeting Link</label>
                  <div style={{ position: 'relative' }}>
                    <input
                      id="meeting-url"
                      type="text"
                      className="form-control"
                      placeholder="https://teams.microsoft.com/l/meetup-join/..."
                      value={meetingUrl}
                      onChange={(e) => setMeetingUrl(e.target.value)}
                      disabled={isSubmitting}
                      style={{ paddingLeft: '2.5rem' }}
                    />
                    <LinkIcon size={18} style={{ position: 'absolute', left: '0.85rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
                  </div>
                </div>

                <div className="form-group">
                  <label className="form-label" htmlFor="subject">Meeting Topic / Title (Optional)</label>
                  <input
                    id="subject"
                    type="text"
                    className="form-control"
                    placeholder="Live Client Discussion / Sprint Planning"
                    value={subject}
                    onChange={(e) => setSubject(e.target.value)}
                    disabled={isSubmitting}
                  />
                </div>

                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={isSubmitting}
                  style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.5rem' }}
                >
                  <PhoneCall size={18} />
                  <span>{isSubmitting ? 'Connecting Bot to Teams & Media Worker...' : 'Dispatch Bot to Live Meeting'}</span>
                </button>
              </form>
            ) : (
              /* Schedule Meeting Form */
              <form onSubmit={handleScheduleSubmit}>
                <div className="form-group">
                  <label className="form-label" htmlFor="sched-meeting-url">Teams Meeting Link / Meeting ID</label>
                  <div style={{ position: 'relative' }}>
                    <input
                      id="sched-meeting-url"
                      type="text"
                      className="form-control"
                      placeholder="https://teams.microsoft.com/l/meetup-join/..."
                      value={meetingUrl}
                      onChange={(e) => setMeetingUrl(e.target.value)}
                      disabled={isSubmitting}
                      style={{ paddingLeft: '2.5rem' }}
                    />
                    <LinkIcon size={18} style={{ position: 'absolute', left: '0.85rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
                  </div>
                </div>

                <div className="grid-2">
                  <div className="form-group">
                    <label className="form-label" htmlFor="scheduled-date">Scheduled Date</label>
                    <div style={{ position: 'relative' }}>
                      <input
                        id="scheduled-date"
                        type="date"
                        className="form-control"
                        value={scheduledDate}
                        onChange={(e) => setScheduledDate(e.target.value)}
                        disabled={isSubmitting}
                        style={{ paddingLeft: '2.5rem' }}
                      />
                      <Calendar size={18} style={{ position: 'absolute', left: '0.85rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
                    </div>
                  </div>

                  <div className="form-group">
                    <label className="form-label" htmlFor="scheduled-time">Scheduled Time</label>
                    <div style={{ position: 'relative' }}>
                      <input
                        id="scheduled-time"
                        type="time"
                        className="form-control"
                        value={scheduledTime}
                        onChange={(e) => setScheduledTime(e.target.value)}
                        disabled={isSubmitting}
                        style={{ paddingLeft: '2.5rem' }}
                      />
                      <Clock size={18} style={{ position: 'absolute', left: '0.85rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
                    </div>
                  </div>
                </div>

                <div className="grid-2">
                  <div className="form-group">
                    <label className="form-label" htmlFor="organizer-email">Organizer Email</label>
                    <div style={{ position: 'relative' }}>
                      <input
                        id="organizer-email"
                        type="email"
                        className="form-control"
                        placeholder="organizer@company.com"
                        value={organizerEmail}
                        onChange={(e) => setOrganizerEmail(e.target.value)}
                        disabled={isSubmitting}
                        style={{ paddingLeft: '2.5rem' }}
                      />
                      <Mail size={18} style={{ position: 'absolute', left: '0.85rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
                    </div>
                  </div>

                  <div className="form-group">
                    <label className="form-label" htmlFor="sched-subject">Meeting Title / Topic (Optional)</label>
                    <input
                      id="sched-subject"
                      type="text"
                      className="form-control"
                      placeholder="Weekly Engineering Sync"
                      value={subject}
                      onChange={(e) => setSubject(e.target.value)}
                      disabled={isSubmitting}
                    />
                  </div>
                </div>

                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={isSubmitting}
                >
                  {isSubmitting ? 'Scheduling Meeting...' : 'Schedule Meeting'}
                </button>
              </form>
            )}
          </div>

          {/* Upcoming Meetings List */}
          <div className="card">
            <div className="card-header">
              <h2 className="card-title">
                <Video size={20} style={{ color: 'var(--secondary)' }} />
                Upcoming Meetings
              </h2>
              <button onClick={loadData} className="btn btn-secondary btn-sm" title="Refresh">
                <RefreshCw size={14} className={isLoadingMeetings ? 'spin' : ''} />
                <span>Refresh</span>
              </button>
            </div>

            {isLoadingMeetings ? (
              <div className="loading-spinner">
                <div className="spinner"></div>
                <span>Syncing with Microsoft Graph Calendar...</span>
              </div>
            ) : meetingsError ? (
              <div className="alert alert-error">
                <AlertCircle size={18} />
                <div>{meetingsError}</div>
              </div>
            ) : upcomingMeetings.length === 0 ? (
              <div className="empty-state">
                <div className="empty-state-icon">📅</div>
                <p>No upcoming meetings found in the calendar.</p>
                <span style={{ fontSize: '0.85rem', color: 'var(--text-dim)' }}>
                  Submit a meeting above or create an event in Outlook to let the Graph Watcher detect it.
                </span>
              </div>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table className="custom-table">
                  <thead>
                    <tr>
                      <th>Title</th>
                      <th>Date / Time</th>
                      <th>Organizer</th>
                      <th>Status</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {upcomingMeetings.map((mtg, idx) => (
                      <tr key={mtg.id || mtg.event_id || idx}>
                        <td style={{ fontWeight: 600, color: '#fff' }}>
                          {mtg.subject || mtg.title || 'Teams Meeting'}
                        </td>
                        <td>
                          {mtg.start_time || mtg.scheduled_at || (mtg.start ? `${mtg.start.dateTime || mtg.start}` : 'Upcoming')}
                        </td>
                        <td>{mtg.organizer || mtg.organizer_email || 'N/A'}</td>
                        <td>
                          <StatusBadge status={mtg.status || 'Scheduled'} />
                        </td>
                        <td>
                          <Link
                            to={`/summary/${mtg.id || mtg.event_id}`}
                            className="btn btn-outline-primary btn-sm"
                          >
                            <span>Details</span>
                            <ChevronRight size={14} />
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>

        {/* Right Sidebar: Diagnostic Controls & Graph Watcher Status */}
        <div>
          <div className="card">
            <div className="card-header">
              <h3 className="card-title" style={{ fontSize: '1rem' }}>
                <Server size={18} style={{ color: 'var(--accent-purple)' }} />
                Graph Calendar Watcher
              </h3>
            </div>

            <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '1.25rem' }}>
              The background watcher automatically polls Microsoft Graph every minute to detect scheduled meetings, join calls, record audio, transcribe, and generate Claude summaries.
            </div>

            <div style={{ background: 'rgba(11,15,25,0.7)', borderRadius: 'var(--radius-sm)', padding: '1rem', marginBottom: '1.25rem', border: '1px solid var(--border-color)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
                <span style={{ color: 'var(--text-dim)' }}>Watcher Service:</span>
                <span className="badge badge-completed">
                  {schedulerInfo?.running ? 'RUNNING' : 'ACTIVE'}
                </span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
                <span style={{ color: 'var(--text-dim)' }}>Poll Interval:</span>
                <span style={{ color: '#fff', fontWeight: 600 }}>60 seconds</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--text-dim)' }}>Active Jobs:</span>
                <span style={{ color: '#fff', fontWeight: 600 }}>
                  {schedulerInfo?.jobs ? schedulerInfo.jobs.length : 1}
                </span>
              </div>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              <button
                onClick={handlePollNow}
                className="btn btn-secondary"
                disabled={isPollingNow}
                style={{ width: '100%' }}
              >
                <RefreshCw size={16} className={isPollingNow ? 'spin' : ''} />
                <span>{isPollingNow ? 'Polling Graph API...' : 'Trigger Poll Now'}</span>
              </button>

              <div style={{ display: 'flex', gap: '0.5rem' }}>
                <button
                  onClick={() => handleToggleScheduler('start')}
                  className="btn btn-secondary btn-sm"
                  style={{ flex: 1 }}
                >
                  <Play size={14} style={{ color: 'var(--accent-emerald)' }} />
                  <span>Start</span>
                </button>

                <button
                  onClick={() => handleToggleScheduler('stop')}
                  className="btn btn-secondary btn-sm"
                  style={{ flex: 1 }}
                >
                  <Square size={14} style={{ color: 'var(--accent-rose)' }} />
                  <span>Pause</span>
                </button>
              </div>
            </div>
          </div>

          {/* Active Calls & Media Worker Bridge Card */}
          <div className="card" style={{ marginTop: '1.5rem' }}>
            <div className="card-header">
              <h3 className="card-title" style={{ fontSize: '1rem' }}>
                <PhoneCall size={18} style={{ color: 'var(--accent-emerald)' }} />
                Active Bot Calls
              </h3>
              <span className="badge badge-completed">{activeCalls.length} Active</span>
            </div>

            {activeCalls.length === 0 ? (
              <div style={{ fontSize: '0.825rem', color: 'var(--text-dim)', textAlign: 'center', padding: '1rem 0' }}>
                No active meeting calls currently connected to Media Worker.
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                {activeCalls.map((call, idx) => (
                  <div key={call.call_id || idx} style={{ background: 'rgba(11,15,25,0.7)', borderRadius: 'var(--radius-sm)', padding: '0.75rem', border: '1px solid var(--border-color)' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.25rem' }}>
                      <span style={{ fontSize: '0.8rem', fontWeight: 600, color: '#fff' }}>Call ID: {call.call_id ? call.call_id.slice(0, 10) + '...' : 'Live'}</span>
                      <StatusBadge status={call.state || 'Established'} />
                    </div>
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                      Started: {call.started_at ? new Date(call.started_at).toLocaleTimeString() : 'Just now'}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default MeetingSetupPage;
