import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { FileText, Calendar, Search, RefreshCw, ChevronRight, AlertCircle, Clock } from 'lucide-react';
import StatusBadge from '../components/StatusBadge';
import { getPastMeetings } from '../api/meetingsApi';

const PastMeetingsPage = () => {
  const [meetings, setMeetings] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');
  const [searchTerm, setSearchTerm] = useState('');

  const fetchMeetings = async () => {
    setIsLoading(true);
    setError('');
    try {
      const data = await getPastMeetings();
      const list = Array.isArray(data) ? data : data?.meetings || data?.items || [];
      setMeetings(list);
    } catch (err) {
      console.error('Failed to load past meetings:', err);
      setError(err.response?.data?.detail || err.message || 'Failed to retrieve meeting summaries');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchMeetings();
  }, []);

  const filteredMeetings = meetings.filter((mtg) => {
    const term = searchTerm.toLowerCase();
    const title = (mtg.title || mtg.subject || '').toLowerCase();
    const organizer = (mtg.organizer || mtg.organizer_email || '').toLowerCase();
    const eventId = (mtg.event_id || mtg.id || '').toLowerCase();
    return title.includes(term) || organizer.includes(term) || eventId.includes(term);
  });

  return (
    <div>
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '1rem' }}>
        <div>
          <h1 className="page-title">Processed Meeting Summaries</h1>
          <p className="page-subtitle">
            Browse AI-generated summaries, executive overviews, action items, audio recordings, and transcripts.
          </p>
        </div>

        <button onClick={fetchMeetings} className="btn btn-secondary">
          <RefreshCw size={16} className={isLoading ? 'spin' : ''} />
          <span>Refresh List</span>
        </button>
      </div>

      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <div style={{ position: 'relative' }}>
          <input
            type="text"
            className="form-control"
            placeholder="Search by title, organizer, or meeting ID..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            style={{ paddingLeft: '2.5rem' }}
          />
          <Search size={18} style={{ position: 'absolute', left: '0.85rem', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
        </div>
      </div>

      {isLoading ? (
        <div className="loading-spinner">
          <div className="spinner"></div>
          <span>Loading meeting summaries from TekMeet backend...</span>
        </div>
      ) : error ? (
        <div className="alert alert-error">
          <AlertCircle size={18} />
          <div>{error}</div>
        </div>
      ) : filteredMeetings.length === 0 ? (
        <div className="card empty-state">
          <div className="empty-state-icon">📝</div>
          <h3>No Meeting Summaries Found</h3>
          <p style={{ marginTop: '0.5rem' }}>
            {searchTerm ? 'No meetings match your search query.' : 'No processed meetings recorded yet.'}
          </p>
        </div>
      ) : (
        <div className="grid-3">
          {filteredMeetings.map((mtg, idx) => {
            const eventId = mtg.event_id || mtg.id || `meeting-${idx}`;
            const title = mtg.title || mtg.subject || 'Teams Meeting Session';
            const dateStr = mtg.date || mtg.created_at || mtg.scheduled_at || 'Recently Processed';
            const status = mtg.status || mtg.processing_status || 'Completed';

            return (
              <div key={eventId} className="card" style={{ display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '0.75rem' }}>
                    <StatusBadge status={status} />
                    <span style={{ fontSize: '0.775rem', color: 'var(--text-dim)', display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                      <Clock size={12} />
                      {dateStr}
                    </span>
                  </div>

                  <h3 style={{ fontSize: '1.1rem', fontWeight: 700, color: '#fff', marginBottom: '0.5rem', lineHeight: '1.4' }}>
                    {title}
                  </h3>

                  {mtg.organizer_email && (
                    <p style={{ fontSize: '0.825rem', color: 'var(--text-muted)', marginBottom: '0.75rem' }}>
                      Organizer: {mtg.organizer_email}
                    </p>
                  )}

                  {mtg.summary_preview && (
                    <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden', marginBottom: '1rem' }}>
                      {mtg.summary_preview}
                    </p>
                  )}
                </div>

                <div style={{ paddingTop: '1rem', borderTop: '1px solid var(--border-color)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontSize: '0.75rem', color: 'var(--text-dim)', fontFamily: 'JetBrains Mono, monospace' }}>
                    ID: {eventId.slice(0, 12)}...
                  </span>

                  <Link to={`/summary/${eventId}`} className="btn btn-primary btn-sm">
                    <span>View Summary</span>
                    <ChevronRight size={14} />
                  </Link>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default PastMeetingsPage;
