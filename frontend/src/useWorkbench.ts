import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ApiError,
  candidatePage,
  errorMessage,
  isCancelled,
  noContent,
  mutate,
  noticePage,
  parseCandidate,
  parseChange,
  parseId,
  parseSession,
  request,
} from './api';
import type { Candidate, Notice, Page, RefreshSession, Session } from './api';

export function useWorkbench(
  session: Session,
  onSessionChange: (session: Session) => void,
  refreshSession: RefreshSession,
) {
  const [candidates, setCandidates] = useState<Page<Candidate>>({ items: [], nextCursor: null });
  const [notices, setNotices] = useState<Page<Notice>>({ items: [], nextCursor: null });
  const [records, setRecords] = useState<Record<string, Candidate | null>>({});
  const [loading, setLoading] = useState(true);
  const [problem, setProblem] = useState('');
  const [announcement, setAnnouncement] = useState('');
  const [reconciled, setReconciled] = useState(0);
  const [connection, setConnection] = useState<
    'connecting' | 'connected' | 'reconnecting' | 'offline'
  >('connecting');
  const [attempt, setAttempt] = useState(0);
  const scope = useRef<AbortController | null>(null);
  const candidatePageSequence = useRef(0);
  const noticePageSequence = useRef(0);
  const candidateReads = useRef(new Map<string, AbortController>());

  // Detail and stream reads share cancellation so an older response cannot win.
  const trackCandidateRead = useCallback((id: string, read: AbortController) => {
    candidateReads.current.get(id)?.abort();
    candidateReads.current.set(id, read);
    return () => {
      if (candidateReads.current.get(id) === read) candidateReads.current.delete(id);
    };
  }, []);

  const remember = useCallback((candidate: Candidate | null, id: string) => {
    setRecords((previous) => {
      const known = previous[id];
      if (known === null && candidate !== null) return previous;
      // A trusted event replay can reopen a failed completion handler.
      const rank = { Queued: 0, Screening: 1, Completed: 2, Failed: 1 };
      if (known && candidate && rank[known.status] > rank[candidate.status]) return previous;
      const next = { ...previous };
      delete next[id];
      next[id] = candidate;
      const first = Object.keys(next)[0];
      if (Object.keys(next).length > 100 && first) delete next[first];
      return next;
    });
  }, []);
  const report = useCallback(
    (error: unknown) => {
      if (error instanceof ApiError && (error.status === 401 || error.code === 'session_expired')) {
        void refreshSession(true);
        return;
      }
      if (!isCancelled(error)) setProblem(errorMessage(error));
    },
    [refreshSession],
  );
  const loadCandidates = useCallback(
    async (cursor: string | null = null) => {
      const current = scope.current;
      const sequence = ++candidatePageSequence.current;
      if (!current) return;
      try {
        const page = await request(
          `/api/candidates${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`,
          candidatePage,
          { signal: current.signal },
        );
        if (
          scope.current === current &&
          !current.signal.aborted &&
          candidatePageSequence.current === sequence
        )
          setCandidates(page);
      } catch (error) {
        if (scope.current === current && candidatePageSequence.current === sequence) report(error);
      }
    },
    [report],
  );
  const loadNotices = useCallback(
    async (cursor: string | null = null) => {
      const current = scope.current;
      const sequence = ++noticePageSequence.current;
      if (!current) return;
      try {
        const page = await request(
          `/api/notifications${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`,
          noticePage,
          { signal: current.signal },
        );
        if (
          scope.current === current &&
          !current.signal.aborted &&
          noticePageSequence.current === sequence
        )
          setNotices(page);
      } catch (error) {
        if (scope.current === current && noticePageSequence.current === sequence) report(error);
      }
    },
    [report],
  );

  useEffect(() => {
    const controller = new AbortController();
    scope.current = controller;
    const reads = candidateReads.current;
    let active = true;
    let verifiedOwner = false;
    let reconciling = false;
    let draining = false;
    let checkedError = false;
    let reconcilePending = false;
    const pending = new Set<string>();
    const source = new EventSource('/api/events', { withCredentials: true });

    async function reconcile() {
      if (!active) return;
      if (draining) {
        reconcilePending = true;
        return;
      }
      if (reconciling) return;
      reconciling = true;
      const candidateSequence = ++candidatePageSequence.current;
      const noticeSequence = ++noticePageSequence.current;
      try {
        const [candidateList, noticeList] = await Promise.all([
          request('/api/candidates', candidatePage, { signal: controller.signal }),
          request('/api/notifications', noticePage, { signal: controller.signal }),
        ]);
        if (!active) return;
        if (candidatePageSequence.current === candidateSequence) setCandidates(candidateList);
        if (noticePageSequence.current === noticeSequence) setNotices(noticeList);
        setProblem('');
        setReconciled((value) => value + 1);
      } catch (error) {
        if (active) report(error);
      } finally {
        reconciling = false;
        if (active) {
          setLoading(false);
          void drain();
        }
      }
    }
    async function verifyOwner() {
      verifiedOwner = false;
      try {
        const current = await request('/api/session', parseSession, {
          signal: controller.signal,
        });
        if (!active) return;
        if (current.ownerId !== session.ownerId) {
          source.close();
          onSessionChange(current);
          return;
        }
        onSessionChange(current);
        verifiedOwner = true;
        setConnection(source.readyState === 1 ? 'connected' : 'reconnecting');
        await reconcile();
        void drain();
      } catch (error) {
        if (active) report(error);
      }
    }
    async function drain() {
      if (draining || reconciling || !active || !verifiedOwner) return;
      draining = true;
      try {
        while (pending.size && active && verifiedOwner) {
          const id = pending.values().next().value;
          if (!id) break;
          pending.delete(id);
          const read = new AbortController();
          const cancel = () => read.abort();
          controller.signal.addEventListener('abort', cancel, { once: true });
          const untrack = trackCandidateRead(id, read);
          try {
            const candidate = await request(`/api/candidates/${id}`, parseCandidate, {
              signal: read.signal,
            });
            if (!active || !verifiedOwner || read.signal.aborted) continue;
            untrack();
            candidatePageSequence.current++;
            remember(candidate, id);
            setCandidates((previous) => ({
              ...previous,
              items: previous.items.map((item) => (item.id === id ? candidate : item)),
            }));
            if (candidate.status === 'Completed' || candidate.status === 'Failed') {
              const noticeSequence = ++noticePageSequence.current;
              const page = await request('/api/notifications', noticePage, {
                signal: read.signal,
              });
              if (!active || !verifiedOwner || read.signal.aborted) continue;
              if (noticePageSequence.current === noticeSequence) {
                setNotices(page);
                setAnnouncement(
                  `${candidate.displayName}: ${candidate.status === 'Completed' ? 'evidence is ready for review' : 'screening could not finish'}. Open the inbox when you are ready.`,
                );
              }
            }
          } catch (error) {
            if (!active || read.signal.aborted || isCancelled(error)) continue;
            if (
              error instanceof ApiError &&
              (error.status === 404 || error.status === 410 || error.code === 'not_found')
            ) {
              candidatePageSequence.current++;
              noticePageSequence.current++;
              remember(null, id);
              setCandidates((previous) => ({
                ...previous,
                items: previous.items.filter((item) => item.id !== id),
              }));
              setNotices((previous) => ({
                ...previous,
                items: previous.items.filter((item) => item.candidateId !== id),
              }));
            } else report(error);
          } finally {
            controller.signal.removeEventListener('abort', cancel);
            untrack();
          }
        }
      } finally {
        draining = false;
        if (reconcilePending) {
          reconcilePending = false;
          void reconcile();
        }
      }
    }
    source.addEventListener('open', () => {
      checkedError = false;
      void verifyOwner();
    });
    source.addEventListener('change', (event) => {
      if (!active) return;
      try {
        if (
          !(event instanceof MessageEvent) ||
          typeof event.data !== 'string' ||
          event.data.length > 256
        )
          throw new ApiError(0, 'invalid_response');
        const data: unknown = JSON.parse(event.data);
        const id = parseChange(data);
        if (pending.size >= 128) {
          pending.clear();
          void reconcile();
        }
        pending.add(id);
        void drain();
      } catch (error) {
        report(error);
      }
    });
    source.addEventListener('reconcile', () => {
      if (verifiedOwner) void reconcile();
    });
    source.addEventListener('error', () => {
      verifiedOwner = false;
      if (active) setConnection(navigator.onLine ? 'reconnecting' : 'offline');
      if (active && !checkedError && navigator.onLine) {
        checkedError = true;
        void verifyOwner();
      }
    });
    const online = () => {
      void verifyOwner();
    };
    const offline = () => {
      if (active) setConnection('offline');
    };
    const focus = () => {
      if (navigator.onLine) void verifyOwner();
    };
    window.addEventListener('online', online);
    window.addEventListener('offline', offline);
    window.addEventListener('focus', focus);
    // Subscribe before the initial bounded snapshot; reconnect repeats the durable read.
    void reconcile();
    return () => {
      active = false;
      controller.abort();
      source.close();
      reads.forEach((read) => read.abort());
      reads.clear();
      window.removeEventListener('online', online);
      window.removeEventListener('offline', offline);
      window.removeEventListener('focus', focus);
    };
  }, [session.ownerId, attempt, onSessionChange, remember, report, trackCandidateRead]);

  useEffect(() => {
    const dated = [
      ...candidates.items,
      ...Object.values(records).filter((item): item is Candidate => item !== null),
    ];
    if (!dated.length) return;
    const nearest = Math.min(...dated.map((item) => Date.parse(item.expiresAt)));
    const timer = window.setTimeout(
      () => {
        const expired = new Set(
          dated.filter((item) => Date.parse(item.expiresAt) <= Date.now()).map((item) => item.id),
        );
        if (!expired.size) return;
        setRecords((previous) => {
          const next = { ...previous };
          expired.forEach((id) => {
            next[id] = null;
          });
          return next;
        });
        setCandidates((previous) => ({
          ...previous,
          items: previous.items.filter((item) => !expired.has(item.id)),
        }));
        setNotices((previous) => ({
          ...previous,
          items: previous.items.filter((item) => !expired.has(item.candidateId)),
        }));
      },
      Math.min(Math.max(0, nearest - Date.now()), 2147483647),
    );
    return () => window.clearTimeout(timer);
  }, [candidates.items, records]);

  const accepted = useCallback(
    (candidate: Candidate) => {
      remember(candidate, candidate.id);
      setCandidates((previous) => ({
        ...previous,
        items: [candidate, ...previous.items.filter((item) => item.id !== candidate.id)].slice(
          0,
          100,
        ),
      }));
    },
    [remember],
  );
  const deleteCandidate = useCallback(
    async (id: string, signal: AbortSignal) => {
      await mutate(
        `/api/candidates/${parseId(id)}`,
        (value) => {
          if (
            !value ||
            typeof value !== 'object' ||
            !('id' in value) ||
            !('status' in value) ||
            parseId(value.id) !== id ||
            value.status !== 'Deleting'
          )
            throw new ApiError(0, 'invalid_response');
          return undefined;
        },
        { method: 'DELETE', signal },
        session,
        refreshSession,
      );
      candidatePageSequence.current++;
      noticePageSequence.current++;
      candidateReads.current.get(id)?.abort();
      remember(null, id);
      setCandidates((previous) => ({
        ...previous,
        items: previous.items.filter((item) => item.id !== id),
      }));
      setNotices((previous) => ({
        ...previous,
        items: previous.items.filter((item) => item.candidateId !== id),
      }));
      setAnnouncement(
        'Deletion accepted. This submission and its notices are hidden while cleanup finishes.',
      );
    },
    [session, refreshSession, remember],
  );
  const markRead = useCallback(
    async (id: string) => {
      const current = scope.current;
      if (!current) return;
      try {
        await mutate(
          `/api/notifications/${parseId(id)}/read`,
          noContent,
          { method: 'POST', signal: current.signal },
          session,
          refreshSession,
        );
        if (scope.current !== current || current.signal.aborted) return;
        noticePageSequence.current++;
        setNotices((previous) => ({
          ...previous,
          items: previous.items.map((item) => (item.id === id ? { ...item, read: true } : item)),
        }));
      } catch (error) {
        report(error);
      }
    },
    [session, refreshSession, report],
  );

  return {
    candidates,
    notices,
    records,
    reconciled,
    loading,
    problem,
    announcement,
    connection,
    remember,
    trackCandidateRead,
    accepted,
    deleteCandidate,
    markRead,
    loadCandidates,
    loadNotices,
    retry: () => setAttempt((value) => value + 1),
  };
}
