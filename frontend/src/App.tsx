import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { Link, NavLink, Route, Routes, useLocation, useNavigate, useParams } from 'react-router';
import {
  ApiError,
  errorMessage,
  isCancelled,
  mutate,
  noContent,
  parseAccepted,
  parseCandidate,
  parseId,
  parseResult,
  parseSession,
  request,
} from './api';
import type { EvidenceResult, RefreshSession, Session, Status, Submission } from './api';
import { useWorkbench } from './useWorkbench';

type Workbench = ReturnType<typeof useWorkbench>;
const statusLabels: Record<Status, string> = {
  Queued: 'Queued',
  Screening: 'Screening',
  Completed: 'Ready for review',
  Failed: 'Needs attention',
};
const dateFormat = new Intl.DateTimeFormat(undefined, {
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
});
const formatDate = (value: string) => dateFormat.format(new Date(value));

function Icon({ name }: { name: 'plus' | 'list' | 'inbox' | 'arrow' | 'check' }) {
  const paths = {
    plus: 'M12 5v14M5 12h14',
    list: 'M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01',
    inbox: 'M4 5h16v14H4zM4 13h5l1 3h4l1-3h5',
    arrow: 'M5 12h14m-6-6 6 6-6 6',
    check: 'm5 12 4 4L19 6',
  };
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name]} />
    </svg>
  );
}
function NoticeBox({ children, error = false }: { children: ReactNode; error?: boolean }) {
  return (
    <div className={error ? 'message error' : 'message'} role={error ? 'alert' : undefined}>
      {children}
    </div>
  );
}
function StatusBadge({ status }: { status: Status }) {
  return (
    <span className={`status status-${status.toLowerCase()}`}>
      <span aria-hidden="true" />
      {statusLabels[status]}
    </span>
  );
}
function RouteFocus() {
  const location = useLocation();
  useEffect(() => {
    document.getElementById('page-title')?.focus();
  }, [location.pathname]);
  return null;
}

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [problem, setProblem] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [resetting, setResetting] = useState(false);
  const [resetConfirmation, setResetConfirmation] = useState(false);
  const navigate = useNavigate();
  const channel = useRef<BroadcastChannel | null>(null);
  const resetRequest = useRef<AbortController | null>(null);
  const identityRequest = useRef<AbortController | null>(null);
  const refreshSession = useCallback(() => {
    setSession(null);
    setProblem('');
    setAttempt((value) => value + 1);
  }, []);
  const replaceSession = useCallback((next: Session) => setSession(next), []);
  const refreshIdentity = useCallback(async (fence: boolean): Promise<Session | null> => {
    identityRequest.current?.abort();
    const controller = new AbortController();
    identityRequest.current = controller;
    if (fence) setSession(null);
    try {
      const current = await request('/api/session', parseSession, {
        signal: controller.signal,
      });
      if (controller.signal.aborted) return null;
      setSession(current);
      setProblem('');
      return current;
    } catch (error) {
      if (!isCancelled(error)) setProblem(errorMessage(error));
      return null;
    }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    request('/api/session', parseSession, { signal: controller.signal })
      .then(setSession)
      .catch((error: unknown) => {
        if (!isCancelled(error)) setProblem(errorMessage(error));
      });
    return () => controller.abort();
  }, [attempt]);
  useEffect(() => {
    if (typeof BroadcastChannel === 'undefined') return;
    const bus = new BroadcastChannel('candidate-screening-session');
    channel.current = bus;
    const changed = () => {
      void refreshIdentity(true);
    };
    bus.addEventListener('message', changed);
    return () => {
      bus.close();
      channel.current = null;
    };
  }, [refreshIdentity]);
  useEffect(
    () => () => {
      resetRequest.current?.abort();
      identityRequest.current?.abort();
    },
    [],
  );

  async function reset() {
    if (!session || resetting) return;
    const controller = new AbortController();
    resetRequest.current = controller;
    setResetting(true);
    try {
      await mutate(
        '/api/session',
        noContent,
        { method: 'DELETE', signal: controller.signal },
        session,
        refreshIdentity,
      );
      channel.current?.postMessage('session-reset');
      setResetConfirmation(false);
      navigate('/');
      void refreshIdentity(true);
    } catch (error) {
      if (!isCancelled(error)) setProblem(errorMessage(error));
    } finally {
      if (!controller.signal.aborted) setResetting(false);
    }
  }

  if (!session)
    return (
      <div className="session-screen">
        <div className="wordmark">
          <span className="brand-mark" aria-hidden="true">
            e
          </span>
          Evidence desk
        </div>
        <h1 id="page-title">
          {problem ? 'The workbench is unavailable' : 'Opening your workbench'}
        </h1>
        <p>{problem || 'Establishing this browser’s private synthetic session.'}</p>
        {problem && <button onClick={refreshSession}>Retry connection</button>}
      </div>
    );
  return (
    <>
      <Workspace
        key={session.ownerId}
        session={session}
        onSessionChange={replaceSession}
        onSessionExpired={refreshIdentity}
      >
        <details
          className="session-options"
          open={resetConfirmation}
          onToggle={(event) => setResetConfirmation(event.currentTarget.open)}
        >
          <summary>Browser session</summary>
          <p>
            This is a synthetic browser identity. Starting again removes access to this session’s
            previous work. Its records still expire automatically.
          </p>
          <button
            className="button-secondary"
            disabled={resetting}
            onClick={() => {
              void reset();
            }}
          >
            {resetting ? 'Starting again…' : 'Start a new session'}
          </button>
          {problem && (
            <p role="alert" className="field-error">
              {problem}
            </p>
          )}
        </details>
      </Workspace>
    </>
  );
}

function Workspace({
  session,
  onSessionChange,
  onSessionExpired,
  children,
}: {
  session: Session;
  onSessionChange: (session: Session) => void;
  onSessionExpired: RefreshSession;
  children: ReactNode;
}) {
  const work = useWorkbench(session, onSessionChange, onSessionExpired);
  const unread = work.notices.items.some((item) => !item.read);
  return (
    <div className="workbench">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <aside className="rail">
        <Link to="/" className="wordmark">
          <span className="brand-mark" aria-hidden="true">
            e
          </span>
          Evidence desk
        </Link>
        <nav aria-label="Workbench">
          <NavLink to="/" end>
            <Icon name="plus" />
            New submission
          </NavLink>
          <NavLink to="/submissions">
            <Icon name="list" />
            Worklist
          </NavLink>
          <NavLink to="/inbox" aria-label={unread ? 'Inbox, unread notices' : 'Inbox'}>
            <Icon name="inbox" />
            Inbox
            {unread && (
              <span className="unread-dot" aria-label="Unread notices in the loaded page" />
            )}
          </NavLink>
        </nav>
        <div className="rail-bottom">
          <p className="retention-note">
            Synthetic records only.
            <br />
            Expires after seven days.
            <br />
            Delete earlier at any time.
          </p>
          {children}
        </div>
      </aside>
      <div className="workspace-body">
        <header className="topbar">
          <span>Candidate screening</span>
          <div className={`connection connection-${work.connection}`}>
            <span aria-hidden="true" />
            {work.connection === 'connected'
              ? 'Live updates connected'
              : work.connection === 'offline'
                ? 'Offline · saved work will reconcile'
                : work.connection === 'reconnecting'
                  ? 'Reconnecting · changes are saved'
                  : 'Connecting live updates'}
            {work.connection !== 'connected' && (
              <button className="text-button" onClick={work.retry}>
                Reconnect
              </button>
            )}
          </div>
        </header>
        <main id="main-content" className="main-content">
          <RouteFocus />
          {work.problem && (
            <NoticeBox error>
              {work.problem}{' '}
              <button className="text-button" onClick={work.retry}>
                Refresh saved work
              </button>{' '}
              <button
                className="text-button"
                onClick={() => {
                  void onSessionExpired(false);
                }}
              >
                Refresh session
              </button>
            </NoticeBox>
          )}
          <Routes>
            <Route
              path="/"
              element={
                <NewSubmission session={session} work={work} onSessionExpired={onSessionExpired} />
              }
            />
            <Route path="/submissions" element={<Worklist work={work} />} />
            <Route path="/inbox" element={<Inbox work={work} />} />
            <Route
              path="/processing/:id"
              element={
                <CandidateRoute
                  session={session}
                  work={work}
                  result={false}
                  onSessionExpired={onSessionExpired}
                />
              }
            />
            <Route
              path="/results/:id"
              element={
                <CandidateRoute
                  session={session}
                  work={work}
                  result
                  onSessionExpired={onSessionExpired}
                />
              }
            />
            <Route
              path="*"
              element={
                <>
                  <h1 id="page-title" tabIndex={-1}>
                    Page unavailable
                  </h1>
                  <p>Choose a workbench page to continue.</p>
                  <Link to="/">New submission</Link>
                </>
              }
            />
          </Routes>
        </main>
        <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">
          {work.announcement}
        </div>
      </div>
    </div>
  );
}

function NewSubmission({
  session,
  work,
  onSessionExpired,
}: {
  session: Session;
  work: Workbench;
  onSessionExpired: RefreshSession;
}) {
  const [displayName, setName] = useState('');
  const [profile, setProfile] = useState('');
  const [requirements, setRequirements] = useState('');
  const [problem, setProblem] = useState('');
  const [fields, setFields] = useState<Record<string, string>>({});
  const [pending, setPending] = useState(false);
  const previous = useRef<{ payload: string; key: string } | null>(null);
  const submission = useRef<AbortController | null>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  useEffect(() => () => submission.current?.abort(), []);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const payload: Submission = {
      displayName: displayName.trim(),
      profile: profile.trim(),
      requirements: requirements
        .split('\n')
        .map((value) => value.trim())
        .filter(Boolean),
    };
    const errors: Record<string, string> = {};
    if (!payload.displayName || payload.displayName.length > 80)
      errors.displayName = 'Enter a display name of up to 80 characters.';
    if (!payload.profile || payload.profile.length > 12000)
      errors.profile = 'Enter a synthetic profile of up to 12,000 characters.';
    if (
      !payload.requirements.length ||
      payload.requirements.length > 8 ||
      payload.requirements.some((value) => value.length > 200)
    )
      errors.requirements =
        'Enter one to eight requirements, each on its own line and up to 200 characters.';
    if (
      new Set(payload.requirements.map((value) => value.toLowerCase())).size !==
      payload.requirements.length
    )
      errors.requirements = 'Use distinct requirements; remove repeated lines.';
    setFields(errors);
    setProblem('');
    if (Object.keys(errors).length) {
      setProblem('Check the form before submitting.');
      errorRef.current?.focus();
      return;
    }
    const canonical = JSON.stringify(payload);
    if (previous.current?.payload !== canonical)
      previous.current = { payload: canonical, key: crypto.randomUUID() };
    const controller = new AbortController();
    submission.current = controller;
    setPending(true);
    try {
      const accepted = await mutate(
        '/api/candidates',
        parseAccepted,
        {
          method: 'POST',
          body: payload,
          key: previous.current.key,
          signal: controller.signal,
        },
        session,
        onSessionExpired,
      );
      if (controller.signal.aborted) return;
      work.accepted({ ...accepted, errorCode: null });
      navigate(`/processing/${accepted.id}`);
    } catch (error) {
      if (isCancelled(error)) return;
      if (error instanceof ApiError && (error.status === 401 || error.code === 'session_expired')) {
        void onSessionExpired(true);
        return;
      }
      setProblem(errorMessage(error));
      if (error instanceof ApiError) setFields(error.fields);
      errorRef.current?.focus();
    } finally {
      if (!controller.signal.aborted) setPending(false);
    }
  }
  return (
    <>
      <div className="page-heading">
        <h1 id="page-title" tabIndex={-1}>
          New submission
        </h1>
        <p>
          Bring the profile and the requirements together. Review the evidence before making any
          decision.
        </p>
      </div>
      <div className="submission-layout">
        <form
          className="submission-form"
          onSubmit={(event) => {
            void submit(event);
          }}
          noValidate
        >
          <div
            ref={errorRef}
            tabIndex={-1}
            className={problem ? 'message error' : ''}
            role={problem ? 'alert' : undefined}
          >
            {problem}
          </div>
          <div className="field">
            <label htmlFor="display-name">Candidate display name</label>
            <p id="name-help" className="field-help">
              Use a synthetic name, not a real person’s details.
            </p>
            <input
              id="display-name"
              name="displayName"
              value={displayName}
              onChange={(event) => setName(event.target.value)}
              maxLength={80}
              required
              autoComplete="off"
              aria-describedby={`name-help${fields.displayName ? ' name-error' : ''}`}
              aria-invalid={!!fields.displayName}
            />
            {fields.displayName && (
              <p id="name-error" className="field-error">
                {fields.displayName}
              </p>
            )}
          </div>
          <div className="field">
            <label htmlFor="profile">Candidate profile</label>
            <p id="profile-help" className="field-help">
              Describe relevant experience in plain language. Do not include contact details or
              sensitive information.
            </p>
            <textarea
              id="profile"
              name="profile"
              rows={8}
              value={profile}
              onChange={(event) => setProfile(event.target.value)}
              maxLength={12000}
              required
              aria-describedby={`profile-help${fields.profile ? ' profile-error' : ''}`}
              aria-invalid={!!fields.profile}
            />
            {fields.profile && (
              <p id="profile-error" className="field-error">
                {fields.profile}
              </p>
            )}
          </div>
          <div className="field">
            <label htmlFor="requirements">Role requirements</label>
            <p id="requirements-help" className="field-help">
              One requirement per line. Use up to eight distinct requirements.
            </p>
            <textarea
              id="requirements"
              name="requirements"
              rows={4}
              value={requirements}
              onChange={(event) => setRequirements(event.target.value)}
              maxLength={1607}
              required
              aria-describedby={`requirements-help${fields.requirements ? ' requirements-error' : ''}`}
              aria-invalid={!!fields.requirements}
            />
            {fields.requirements && (
              <p id="requirements-error" className="field-error">
                {fields.requirements}
              </p>
            )}
          </div>
          <p className="submission-disclosure">
            Synthetic records only. Source text, evidence and notices expire after seven days.
            Delete a submission earlier at any time. Human review is always required.
          </p>
          <div className="form-actions">
            <button type="submit" disabled={pending}>
              {pending ? 'Confirming acceptance…' : 'Submit for screening'}
              <Icon name="arrow" />
            </button>
            <p>
              {pending
                ? 'Only this submission is pending. You can navigate to other work.'
                : 'Accepted work continues in the background.'}
            </p>
          </div>
        </form>
        <aside className="review-guide">
          <h2>A review aid, with clear limits</h2>
          <ol>
            <li>
              <strong>Submit once</strong>
              <p>The saved submission gets a status you can return to.</p>
            </li>
            <li>
              <strong>Keep working</strong>
              <p>Leave the processing page. Your inbox holds completion and failure notices.</p>
            </li>
            <li>
              <strong>Check every finding</strong>
              <p>
                Read proposed quotes alongside each requirement. Missing evidence is not a verdict.
              </p>
            </li>
          </ol>
          <div className="guide-footer">
            <Icon name="check" />
            <p>
              Human review is always required. Source text, evidence and notices expire after seven
              days.
            </p>
          </div>
        </aside>
      </div>
    </>
  );
}

function Worklist({ work }: { work: Workbench }) {
  return (
    <>
      <div className="page-heading page-heading-action">
        <div>
          <h1 id="page-title" tabIndex={-1}>
            Worklist
          </h1>
          <p>Saved submissions in this browser session.</p>
        </div>
        <Link className="button" to="/">
          New submission
          <Icon name="plus" />
        </Link>
      </div>
      {work.loading ? (
        <Loading label="Loading saved submissions" />
      ) : work.candidates.items.length ? (
        <div className="worklist">
          <div className="table-heading">
            <span>Candidate</span>
            <span>Status</span>
            <span>Expires</span>
            <span className="sr-only">Open</span>
          </div>
          {work.candidates.items.map((candidate) => (
            <div className="candidate-row" key={candidate.id}>
              <div>
                <strong>{candidate.displayName}</strong>
                <span className="subtle">Submitted {formatDate(candidate.createdAt)}</span>
              </div>
              <StatusBadge status={candidate.status} />
              <time dateTime={candidate.expiresAt}>{formatDate(candidate.expiresAt)}</time>
              <Link
                className="row-link"
                to={
                  candidate.status === 'Completed'
                    ? `/results/${candidate.id}`
                    : `/processing/${candidate.id}`
                }
                aria-label={`Open ${candidate.displayName}`}
              >
                Open
                <Icon name="arrow" />
              </Link>
            </div>
          ))}
        </div>
      ) : (
        <Empty
          title="Your next review starts here"
          text="Submit a synthetic profile and a short list of requirements. Saved work will appear here while it runs."
        />
      )}
      <div className="pagination">
        <button
          className="button-secondary"
          onClick={() => {
            void work.loadCandidates();
          }}
        >
          Latest submissions
        </button>
        {work.candidates.nextCursor && (
          <button
            className="button-secondary"
            onClick={() => {
              void work.loadCandidates(work.candidates.nextCursor);
            }}
          >
            Next page
            <Icon name="arrow" />
          </button>
        )}
      </div>
    </>
  );
}
function Inbox({ work }: { work: Workbench }) {
  return (
    <>
      <div className="page-heading">
        <h1 id="page-title" tabIndex={-1}>
          Inbox
        </h1>
        <p>
          Completion and failure notices stay here when you leave the workbench. Open one when you
          are ready.
        </p>
      </div>
      {work.loading ? (
        <Loading label="Loading saved notices" />
      ) : work.notices.items.length ? (
        <ul className="inbox-list">
          {work.notices.items.map((notice) => (
            <li key={notice.id} className={notice.read ? 'notice-read' : ''}>
              <div className="notice-symbol">
                <Icon name={notice.status === 'Completed' ? 'check' : 'inbox'} />
              </div>
              <div>
                <strong>
                  {notice.status === 'Completed'
                    ? 'Evidence is ready for review'
                    : 'Screening needs attention'}
                </strong>
                <p>
                  {work.records[notice.candidateId]?.displayName ?? 'Saved submission'} ·{' '}
                  <time dateTime={notice.createdAt}>{formatDate(notice.createdAt)}</time>
                </p>
                <span className="notice-read-state">{notice.read ? 'Read' : 'Unread'}</span>
              </div>
              <Link
                className="row-link"
                to={
                  notice.status === 'Completed'
                    ? `/results/${notice.candidateId}`
                    : `/processing/${notice.candidateId}`
                }
                onClick={() => {
                  void work.markRead(notice.id);
                }}
              >
                Open
                <Icon name="arrow" />
              </Link>
              {!notice.read && (
                <button
                  className="text-button"
                  onClick={() => {
                    void work.markRead(notice.id);
                  }}
                >
                  Mark read
                </button>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <Empty
          title="Nothing needs your attention yet"
          text="When screening completes or cannot finish, a saved notice appears here. You do not need to keep a processing page open."
        />
      )}
      <div className="pagination">
        <button
          className="button-secondary"
          onClick={() => {
            void work.loadNotices();
          }}
        >
          Latest notices
        </button>
        {work.notices.nextCursor && (
          <button
            className="button-secondary"
            onClick={() => {
              void work.loadNotices(work.notices.nextCursor);
            }}
          >
            Next page
            <Icon name="arrow" />
          </button>
        )}
      </div>
    </>
  );
}
function Loading({ label }: { label: string }) {
  return (
    <div className="loading-state" role="status">
      <p>{label}…</p>
      <div />
      <div />
      <div />
    </div>
  );
}
function Empty({ title, text }: { title: string; text: string }) {
  return (
    <div className="empty-state">
      <h2>{title}</h2>
      <p>{text}</p>
      <Link to="/">
        Create a submission
        <Icon name="arrow" />
      </Link>
    </div>
  );
}

function CandidateRoute({
  session,
  work,
  result,
  onSessionExpired,
}: {
  session: Session;
  work: Workbench;
  result: boolean;
  onSessionExpired: RefreshSession;
}) {
  const { id } = useParams();
  const [problem, setProblem] = useState('');
  const [loadedEvidence, setLoadedEvidence] = useState<EvidenceResult | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [retry, setRetry] = useState(0);
  const controller = useRef<AbortController | null>(null);
  const navigate = useNavigate();
  const candidate = id ? work.records[id] : undefined;
  const evidence =
    candidate?.status === 'Completed' && loadedEvidence?.candidateId === id ? loadedEvidence : null;
  const { remember, trackCandidateRead } = work;
  useEffect(() => {
    const current = new AbortController();
    controller.current = current;
    let untrack: (() => void) | undefined;
    async function load() {
      try {
        const safeId = parseId(id);
        untrack = trackCandidateRead(safeId, current);
        const summary = await request(`/api/candidates/${safeId}`, parseCandidate, {
          signal: current.signal,
        });
        if (current.signal.aborted) return;
        remember(summary, safeId);
      } catch (error) {
        if (current.signal.aborted || isCancelled(error)) return;
        if (
          error instanceof ApiError &&
          (error.status === 401 || error.code === 'session_expired')
        ) {
          void onSessionExpired(true);
          return;
        }
        if (
          error instanceof ApiError &&
          (error.status === 404 || error.status === 410 || error.code === 'not_found') &&
          id
        )
          remember(null, id);
        setProblem(errorMessage(error));
      } finally {
        untrack?.();
      }
    }
    void load();
    return () => {
      current.abort();
      untrack?.();
    };
  }, [id, retry, work.reconciled, session.ownerId, remember, trackCandidateRead, onSessionExpired]);
  useEffect(() => {
    if (!result || candidate?.status !== 'Completed') return;
    const current = new AbortController();
    controller.current = current;
    async function loadEvidence() {
      try {
        const safeId = parseId(id);
        const data = await request(`/api/candidates/${safeId}/result`, parseResult, {
          signal: current.signal,
        });
        if (current.signal.aborted) return;
        if (data.candidateId !== safeId) throw new ApiError(0, 'invalid_response');
        setLoadedEvidence(data);
        setProblem('');
      } catch (error) {
        if (current.signal.aborted || isCancelled(error)) return;
        if (
          error instanceof ApiError &&
          (error.status === 401 || error.code === 'session_expired')
        ) {
          void onSessionExpired(true);
          return;
        }
        if (
          error instanceof ApiError &&
          (error.status === 404 || error.status === 410 || error.code === 'not_found') &&
          id
        )
          remember(null, id);
        setProblem(errorMessage(error));
      }
    }
    void loadEvidence();
    return () => current.abort();
  }, [
    id,
    result,
    candidate?.status,
    retry,
    work.reconciled,
    session.ownerId,
    remember,
    onSessionExpired,
  ]);
  useEffect(() => {
    if (!result && candidate?.status === 'Completed' && id)
      navigate(`/results/${id}`, { replace: true });
  }, [candidate?.status, result, id, navigate]);

  async function remove() {
    if (!id || deleting) return;
    const current = new AbortController();
    controller.current?.abort();
    controller.current = current;
    setDeleting(true);
    try {
      await work.deleteCandidate(id, current.signal);
      if (!current.signal.aborted) navigate('/submissions');
    } catch (error) {
      if (!isCancelled(error)) setProblem(errorMessage(error));
    } finally {
      if (!current.signal.aborted) setDeleting(false);
    }
  }
  useEffect(() => () => controller.current?.abort(), []);
  if (candidate === null)
    return (
      <>
        <h1 id="page-title" tabIndex={-1}>
          Submission unavailable
        </h1>
        <NoticeBox>
          This item has expired, been deleted, or is not accessible in this session. Its result
          cannot be opened here.
        </NoticeBox>
        <Link to="/submissions">Return to worklist</Link>
      </>
    );
  if (!candidate)
    return (
      <>
        <h1 id="page-title" tabIndex={-1}>
          Opening submission
        </h1>
        {problem ? (
          <NoticeBox error>
            {problem}
            <button className="text-button" onClick={() => setRetry((value) => value + 1)}>
              Retry
            </button>
          </NoticeBox>
        ) : (
          <Loading label="Loading the saved status" />
        )}
      </>
    );
  return (
    <>
      <Link className="back-link" to="/submissions">
        Worklist
      </Link>
      <div className="page-heading page-heading-action">
        <div>
          <h1 id="page-title" tabIndex={-1}>
            {candidate.displayName}
          </h1>
          <p>
            {result
              ? 'Requirement-by-requirement evidence for your review.'
              : 'Your submission is saved. The rest of the workbench stays available.'}
          </p>
        </div>
        <StatusBadge status={candidate.status} />
      </div>
      {problem && (
        <NoticeBox error>
          {problem}
          <button className="text-button" onClick={() => setRetry((value) => value + 1)}>
            Retry request
          </button>
        </NoticeBox>
      )}
      {candidate.status === 'Failed' ? (
        <NoticeBox error>
          Screening could not produce a reliable result. No evidence verdict was generated. You can
          delete this item or submit a new review.
          <p>
            <Link to="/">
              New submission
              <Icon name="arrow" />
            </Link>
          </p>
        </NoticeBox>
      ) : candidate.status !== 'Completed' ? (
        <div className="processing-sheet">
          <ol className="journey">
            <li className="journey-complete">
              <Icon name="check" />
              <div>
                <strong>Submission accepted</strong>
                <p>Your input is saved.</p>
              </div>
            </li>
            <li className={candidate.status === 'Screening' ? 'journey-active' : ''}>
              <span className="journey-marker" aria-hidden="true" />
              <div>
                <strong>
                  {candidate.status === 'Queued'
                    ? 'Waiting for screening'
                    : 'Checking the evidence'}
                </strong>
                <p>
                  {candidate.status === 'Queued'
                    ? 'The saved work will begin when processing is available.'
                    : 'Proposed findings are checked before a result is saved.'}
                </p>
              </div>
            </li>
            <li>
              <span className="journey-marker" aria-hidden="true" />
              <div>
                <strong>Ready for human review</strong>
                <p>A durable notice will appear in your inbox.</p>
              </div>
            </li>
          </ol>
          <div className="continue-work">
            <h2>You can leave this page</h2>
            <p>
              Continue another review. Completion elsewhere will add a notice without redirecting
              you.
            </p>
            <Link className="button" to="/">
              Start another submission
              <Icon name="plus" />
            </Link>
            <Link to="/inbox">
              Open inbox
              <Icon name="arrow" />
            </Link>
          </div>
        </div>
      ) : result ? (
        evidence && evidence.candidateId === id ? (
          <EvidenceSheet evidence={evidence} />
        ) : (
          <Loading label="Opening the evidence" />
        )
      ) : (
        <Link className="button" to={`/results/${candidate.id}`}>
          Open evidence
          <Icon name="arrow" />
        </Link>
      )}
      <footer className="record-footer">
        <p>
          Submitted {formatDate(candidate.createdAt)}
          <br />
          Expires {formatDate(candidate.expiresAt)}
        </p>
        <details
          open={confirmDelete}
          onToggle={(event) => setConfirmDelete(event.currentTarget.open)}
        >
          <summary>Delete submission</summary>
          <p>Remove this source, result and notices before expiry. This action cannot be undone.</p>
          <button
            className="button-danger"
            disabled={deleting}
            onClick={() => {
              void remove();
            }}
          >
            {deleting ? 'Requesting deletion…' : 'Confirm deletion'}
          </button>
        </details>
      </footer>
    </>
  );
}
function EvidenceSheet({ evidence }: { evidence: EvidenceResult }) {
  const states = {
    EvidenceFound: 'Proposed evidence',
    NeedsReview: 'Needs review',
    NotEvidenced: 'Not evidenced',
  };
  return (
    <section className="evidence-sheet" aria-labelledby="evidence-heading">
      <div className="evidence-intro">
        <h2 id="evidence-heading">Evidence, with room for judgment</h2>
        <p>
          Every finding needs human review. A quote can be relevant without proving proficiency; an
          absent quote is not a hiring verdict.
        </p>
      </div>
      <div className="evidence-columns" aria-hidden="true">
        <span>Requirement</span>
        <span>Profile evidence</span>
        <span>Review state</span>
      </div>
      {evidence.items.map((item, index) => (
        <article className="evidence-row" key={`${index}-${item.requirement}`}>
          <h3>{item.requirement}</h3>
          <div>
            {item.evidence ? (
              <blockquote>{item.evidence}</blockquote>
            ) : (
              <p className="no-evidence">No reliable supporting quote was proposed.</p>
            )}
          </div>
          <span className={`evidence-state evidence-${item.state}`}>{states[item.state]}</span>
        </article>
      ))}
      <p className="model-note">
        Prepared with {evidence.provider} · {evidence.model}. Review against the source context
        before using these findings.
      </p>
    </section>
  );
}
