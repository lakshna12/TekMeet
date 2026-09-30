import React, { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  ArrowLeft, Calendar, User, Clock, FileText, Music, CheckSquare,
  Sparkles, Mail, Send, AlertCircle, RefreshCw, Volume2, Info
} from 'lucide-react';
import StatusBadge from '../components/StatusBadge';
import { getMeetingSummaryView } from '../api/meetingsApi';

const SummaryDetailPage = () => {
  const { eventId } = useParams();

  const [data, setData] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');
  const [activeTab, setActiveTab] = useState('summary'); // 'summary' | 'transcript' | 'recording'

  const fetchDetail = async () => {
    setIsLoading(true);
    setError('');
    try {
      const res = await getMeetingSummaryView(eventId);
      setData(res);
    } catch (err) {
      console.error('Failed to load meeting summary detail:', err);
      setError(err.response?.data?.detail || err.message || 'Failed to load summary detail for this meeting');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (eventId) {
      fetchDetail();
    }
  }, [eventId]);

  if (isLoading) {
    return (
      <div className="loading-spinner">
        <div className="spinner"></div>
        <span>Retrieving full meeting summary & transcript artifacts...</span>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div>
        <Link to="/summary" className="btn btn-secondary btn-sm" style={{ marginBottom: '1rem' }}>
          <ArrowLeft size={16} />
          <span>Back to Summaries</span>
        </Link>

        <div className="alert alert-error">
          <AlertCircle size={18} />
          <div>{error || 'Meeting record not found.'}</div>
        </div>
      </div>
    );
  }

  const meeting = data.meeting || {};
  const summary = data.summary || {};
  const transcript = data.transcript || {};
  const recording = data.recording || {};
  const delivery = data.delivery || {};

  // Formatted Structured Summary fields
  const title = meeting.subject || meeting.title || summary.meeting_title || 'Teams Meeting Summary';
  const dateStr = meeting.scheduled_at || meeting.date || summary.date || 'Recorded Session';
  const organizer = meeting.organizer || meeting.organizer_email || summary.organizer || 'N/A';

  const executiveOverview = summary.executive_summary || summary.executive_overview || summary.overview || summary.summary_text;
  const keyPoints = Array.isArray(summary.key_discussion_points || summary.key_points)
    ? (summary.key_discussion_points || summary.key_points)
    : (summary.key_points ? [summary.key_points] : []);

  const decisions = Array.isArray(summary.decisions_made || summary.decisions)
    ? (summary.decisions_made || summary.decisions)
    : (summary.decisions ? [summary.decisions] : []);

  const actionItems = Array.isArray(summary.action_items)
    ? summary.action_items
    : [];

  const rawTranscriptText = transcript.transcript_text || transcript.text || (typeof transcript === 'string' ? transcript : 'No raw transcript recorded.');
  const audioUrl = recording.download_url || recording.file_url || recording.path;

  return (
    <div>
      {/* Top Header Navigation */}
      <div style={{ marginBottom: '1.5rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Link to="/summary" className="btn btn-secondary btn-sm">
          <ArrowLeft size={16} />
          <span>Back to Summaries</span>
        </Link>

        <button onClick={fetchDetail} className="btn btn-secondary btn-sm">
          <RefreshCw size={14} />
          <span>Reload</span>
        </button>
      </div>

      {/* Main Banner */}
      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '1rem' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
              <StatusBadge status={meeting.status || 'Completed'} />
              <span style={{ fontSize: '0.8rem', color: 'var(--text-dim)', fontFamily: 'JetBrains Mono, monospace' }}>
                ID: {eventId}
              </span>
            </div>

            <h1 className="page-title" style={{ fontSize: '1.75rem', marginBottom: '0.5rem' }}>{title}</h1>

            <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap', fontSize: '0.875rem', color: 'var(--text-muted)' }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                <Calendar size={15} style={{ color: 'var(--secondary)' }} />
                {dateStr}
              </span>
              <span style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                <User size={15} style={{ color: 'var(--primary)' }} />
                Organizer: {organizer}
              </span>
              {meeting.duration && (
                <span style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                  <Clock size={15} style={{ color: 'var(--accent-purple)' }} />
                  Duration: {meeting.duration}
                </span>
              )}
            </div>
          </div>

          {/* Delivery Indicators */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem', background: 'rgba(11,15,25,0.6)', padding: '0.75rem 1rem', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-color)' }}>
            <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--text-dim)', textTransform: 'uppercase' }}>
              Automatic Delivery Status
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.825rem' }}>
              <Mail size={14} style={{ color: delivery.email_sent ? 'var(--accent-emerald)' : 'var(--text-dim)' }} />
              <span>Email Summary: {delivery.email_sent ? 'Sent' : 'Pending / Skipped'}</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.825rem' }}>
              <Send size={14} style={{ color: delivery.teams_posted ? 'var(--accent-emerald)' : 'var(--text-dim)' }} />
              <span>Teams Chat Post: {delivery.teams_posted ? 'Posted' : 'Pending'}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1.5rem', borderBottom: '1px solid var(--border-color)', paddingBottom: '0.5rem' }}>
        <button
          className={`btn ${activeTab === 'summary' ? 'btn-primary' : 'btn-secondary'} btn-sm`}
          onClick={() => setActiveTab('summary')}
        >
          <Sparkles size={16} />
          <span>Claude AI Summary</span>
        </button>
        <button
          className={`btn ${activeTab === 'transcript' ? 'btn-primary' : 'btn-secondary'} btn-sm`}
          onClick={() => setActiveTab('transcript')}
        >
          <FileText size={16} />
          <span>Raw Transcript</span>
        </button>
        <button
          className={`btn ${activeTab === 'recording' ? 'btn-primary' : 'btn-secondary'} btn-sm`}
          onClick={() => setActiveTab('recording')}
        >
          <Music size={16} />
          <span>Audio Recording</span>
        </button>
      </div>

      {/* TAB 1: AI SUMMARY */}
      {activeTab === 'summary' && (
        <div className="grid-main-sidebar">
          <div>
            {/* Executive Overview */}
            <div className="card summary-section">
              <h2 className="summary-section-title">
                <Sparkles size={18} />
                Executive Overview
              </h2>
              <p style={{ color: 'var(--text-main)', fontSize: '0.95rem', leading: '1.7' }}>
                {executiveOverview || 'No executive summary text generated.'}
              </p>
            </div>

            {/* Key Discussion Points */}
            <div className="card summary-section">
              <h2 className="summary-section-title">
                <FileText size={18} />
                Key Discussion Points
              </h2>
              {keyPoints.length > 0 ? (
                <ul style={{ paddingLeft: '1.25rem', color: 'var(--text-main)', fontSize: '0.925rem' }}>
                  {keyPoints.map((point, idx) => (
                    <li key={idx} style={{ marginBottom: '0.5rem' }}>
                      {typeof point === 'object' ? point.point || point.description || JSON.stringify(point) : point}
                    </li>
                  ))}
                </ul>
              ) : (
                <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>No specific key discussion points recorded.</p>
              )}
            </div>

            {/* Decisions Made */}
            <div className="card summary-section">
              <h2 className="summary-section-title" style={{ color: 'var(--accent-emerald)' }}>
                <CheckSquare size={18} />
                Decisions Made
              </h2>
              {decisions.length > 0 ? (
                <ul style={{ paddingLeft: '1.25rem', color: 'var(--text-main)', fontSize: '0.925rem' }}>
                  {decisions.map((dec, idx) => (
                    <li key={idx} style={{ marginBottom: '0.5rem' }}>
                      {typeof dec === 'object' ? dec.decision || dec.description || JSON.stringify(dec) : dec}
                    </li>
                  ))}
                </ul>
              ) : (
                <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>No official decisions captured during this meeting.</p>
              )}
            </div>

            {/* Action Items */}
            <div className="card summary-section">
              <h2 className="summary-section-title" style={{ color: 'var(--accent-purple)' }}>
                <CheckSquare size={18} />
                Action Items & Owner Assignments
              </h2>
              {actionItems.length > 0 ? (
                actionItems.map((item, idx) => {
                  const owner = typeof item === 'object' ? (item.owner || item.assignee || 'Unassigned') : 'Assigned';
                  const text = typeof item === 'object' ? (item.task || item.description || item.action || JSON.stringify(item)) : item;
                  const deadline = typeof item === 'object' ? item.deadline || item.due_date : null;

                  return (
                    <div key={idx} className="action-item-card">
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span className="action-owner">Owner: {owner}</span>
                        {deadline && <span style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>Due: {deadline}</span>}
                      </div>
                      <div style={{ color: '#fff', fontSize: '0.9rem', marginTop: '0.25rem' }}>
                        {text}
                      </div>
                    </div>
                  );
                })
              ) : (
                <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>No action items recorded.</p>
              )}
            </div>
          </div>

          {/* Right Sidebar Details */}
          <div>
            <div className="card">
              <h3 className="card-title" style={{ fontSize: '1rem', marginBottom: '1rem' }}>
                <Info size={18} style={{ color: 'var(--secondary)' }} />
                Meeting Metadata
              </h3>

              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem', fontSize: '0.85rem' }}>
                <div>
                  <span style={{ color: 'var(--text-dim)', display: 'block' }}>Event ID</span>
                  <span style={{ fontFamily: 'JetBrains Mono, monospace', color: '#fff', wordBreak: 'break-all' }}>{eventId}</span>
                </div>

                <div>
                  <span style={{ color: 'var(--text-dim)', display: 'block' }}>STT Engine</span>
                  <span style={{ color: '#fff', fontWeight: 600 }}>Deepgram (nova-2)</span>
                </div>

                <div>
                  <span style={{ color: 'var(--text-dim)', display: 'block' }}>Summarizer Engine</span>
                  <span style={{ color: '#fff', fontWeight: 600 }}>Claude 3.5 Sonnet / Gemini Fallback</span>
                </div>

                {audioUrl && (
                  <div style={{ marginTop: '0.5rem', paddingTop: '0.85rem', borderTop: '1px solid var(--border-color)' }}>
                    <span style={{ color: 'var(--text-dim)', display: 'block', marginBottom: '0.35rem' }}>Audio Recording Available</span>
                    <button onClick={() => setActiveTab('recording')} className="btn btn-outline-primary btn-sm" style={{ width: '100%' }}>
                      <Volume2 size={14} />
                      <span>Listen to Audio</span>
                    </button>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* TAB 2: RAW TRANSCRIPT */}
      {activeTab === 'transcript' && (
        <div className="card">
          <div className="card-header">
            <h2 className="card-title">
              <FileText size={20} className="accent" />
              Raw Meeting Transcript
            </h2>
            <span style={{ fontSize: '0.8rem', color: 'var(--text-dim)' }}>
              Deepgram Speech-to-Text Output
            </span>
          </div>

          <div className="transcript-box">
            {rawTranscriptText}
          </div>
        </div>
      )}

      {/* TAB 3: AUDIO RECORDING */}
      {activeTab === 'recording' && (
        <div className="card">
          <div className="card-header">
            <h2 className="card-title">
              <Music size={20} style={{ color: 'var(--accent-purple)' }} />
              Audio Recording Player
            </h2>
          </div>

          {audioUrl ? (
            <div className="audio-player-card">
              <p style={{ fontSize: '0.9rem', color: 'var(--text-muted)', marginBottom: '1rem' }}>
                Playback recorded Teams call audio captured by the Railway Volume worker stream:
              </p>
              <audio controls src={audioUrl}>
                Your browser does not support the audio element.
              </audio>
              <div style={{ marginTop: '1rem', textAlign: 'right' }}>
                <a href={audioUrl} download target="_blank" rel="noreferrer" className="btn btn-secondary btn-sm">
                  Download Audio File (.wav / .mp3)
                </a>
              </div>
            </div>
          ) : (
            <div className="empty-state">
              <div className="empty-state-icon">🎙️</div>
              <p>No audio recording file available for this meeting record.</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default SummaryDetailPage;
