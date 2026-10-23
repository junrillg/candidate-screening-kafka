import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import type { Candidate, Notice } from './api';

const owner = '11111111-1111-4111-8111-111111111111';
const nextOwner = '22222222-2222-4222-8222-222222222222';
const id = '33333333-3333-4333-8333-333333333333';
const noticeId = '44444444-4444-4444-8444-444444444444';
const candidate: Candidate = {
  id,
  displayName: 'Synthetic reviewer sample',
  status: 'Queued',
  createdAt: '2026-10-20T10:00:00Z',
  expiresAt: '2099-10-27T10:00:00Z',
  errorCode: null,
};
class TestEventSource extends EventTarget {
  static instances: TestEventSource[] = [];
  closed = false;
  constructor() {
    super();
    TestEventSource.instances.push(this);
  }
  close() {
    this.closed = true;
  }
  open() {
    this.dispatchEvent(new Event('open'));
  }
  change() {
    this.dispatchEvent(new MessageEvent('change', { data: JSON.stringify({ candidateId: id }) }));
  }
}
function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}
function fakeApi() {
  const state: {
    owner: string;
    csrf: string;
    candidate: Candidate | null;
    notices: Notice[];
    candidateCursor: string | null;
  } = {
    owner,
    csrf: 'synthetic-csrf-a',
    candidateCursor: null,
    candidate: null,
    notices: [],
  };
  const submit = vi.fn<(options: RequestInit) => Promise<Response>>(async () => {
    state.candidate = { ...candidate };
    return json(candidate, 202);
  });
  const fetcher = vi.fn<typeof fetch>(async (input, options = {}) => {
    const url = String(input);
    if (url === '/api/session') {
      if (options.method === 'DELETE') {
        state.owner = nextOwner;
        state.csrf = 'synthetic-csrf-b';
        state.candidate = null;
        state.notices = [];
        return new Response(null, { status: 204 });
      }
      return json({ ownerId: state.owner, csrfToken: state.csrf });
    }
    if (url === '/api/candidates' && options.method === 'POST') return submit(options);
    if (url.startsWith('/api/candidates?') || url === '/api/candidates')
      return json({
        items: state.candidate ? [state.candidate] : [],
        nextCursor: state.candidateCursor,
      });
    if (url === `/api/candidates/${id}/result`)
      return json({
        candidateId: id,
        items: [
          {
            requirement: 'Build APIs',
            evidence: 'Built APIs with .NET.',
            state: 'EvidenceFound',
          },
        ],
        reviewRequired: true,
        provider: 'Ollama',
        model: 'local-model',
      });
    if (url === `/api/candidates/${id}`) {
      if (options.method === 'DELETE') {
        state.candidate = null;
        state.notices = [];
        return json({ id, status: 'Deleting' }, 202);
      }
      return state.candidate
        ? json(state.candidate)
        : json({ error: { code: 'not_found', message: 'Unavailable.' } }, 404);
    }
    if (url.startsWith('/api/notifications/')) return new Response(null, { status: 204 });
    if (url.startsWith('/api/notifications'))
      return json({ items: state.notices, nextCursor: null });
    return json({ error: { code: 'not_found', message: 'Unavailable.' } }, 404);
  });
  vi.stubGlobal('fetch', fetcher);
  return { state, submit, fetcher };
}
async function fillSubmission(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole('heading', { name: 'New submission' });
  await user.type(screen.getByLabelText('Candidate display name'), candidate.displayName);
  await user.type(screen.getByLabelText('Candidate profile'), 'Built APIs with .NET.');
  await user.type(screen.getByLabelText('Role requirements'), 'Build APIs');
}

beforeEach(() => {
  TestEventSource.instances = [];
  vi.stubGlobal('EventSource', TestEventSource);
  vi.stubGlobal('BroadcastChannel', undefined);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('submission and asynchronous navigation', () => {
  it('shows processing again when an operator replays a failed completion', async () => {
    const api = fakeApi();
    api.state.candidate = { ...candidate, status: 'Failed', errorCode: 'inference_failed' };
    render(
      <MemoryRouter initialEntries={[`/processing/${id}`]}>
        <App />
      </MemoryRouter>,
    );
    await screen.findByText(/No evidence verdict was generated/);
    const source = TestEventSource.instances.at(-1);
    await act(async () => {
      source?.open();
    });
    api.state.candidate = { ...candidate, status: 'Screening' };
    await act(async () => {
      source?.change();
    });
    await waitFor(() => expect(screen.queryByText(/No evidence verdict was generated/)).toBeNull());
    expect(screen.queryByText('Proposed evidence')).toBeNull();
    let finishNotices: ((value: Response) => void) | undefined;
    let noticeSignal: AbortSignal | null | undefined;
    const delayedNotices = new Promise<Response>((resolve) => {
      finishNotices = resolve;
    });
    const normal = api.fetcher.getMockImplementation();
    api.fetcher.mockImplementation((input, options) => {
      if (String(input) === '/api/notifications') {
        noticeSignal = options?.signal;
        return delayedNotices;
      }
      return normal ? normal(input, options) : Promise.reject(new Error('Missing test handler.'));
    });
    api.state.candidate = { ...candidate, status: 'Completed' };
    api.state.notices = [
      {
        id: noticeId,
        candidateId: id,
        status: 'Completed',
        read: false,
        createdAt: candidate.createdAt,
      },
    ];
    await act(async () => {
      source?.change();
    });
    await screen.findByText('Built APIs with .NET.');
    expect(noticeSignal?.aborted).toBe(false);
    await act(async () => {
      finishNotices?.(json({ items: api.state.notices, nextCursor: null }));
    });
    await screen.findByRole('link', { name: 'Inbox, unread notices' });
    await waitFor(() =>
      expect(screen.getByRole('status').textContent).toContain('evidence is ready'),
    );
  });

  it('rejects a late failed detail read and loads replayed evidence on the existing result route', async () => {
    const api = fakeApi();
    const failed: Candidate = { ...candidate, status: 'Failed', errorCode: 'inference_failed' };
    api.state.candidate = failed;
    render(
      <MemoryRouter initialEntries={[`/results/${id}`]}>
        <App />
      </MemoryRouter>,
    );
    await screen.findByText(/No evidence verdict was generated/);
    let finish: ((value: Response) => void) | undefined;
    let delayedSignal: AbortSignal | null | undefined;
    let delayDetail = true;
    const delayed = new Promise<Response>((resolve) => {
      finish = resolve;
    });
    const normal = api.fetcher.getMockImplementation();
    api.fetcher.mockImplementation((input, options) => {
      if (String(input) === `/api/candidates/${id}` && delayDetail) {
        delayDetail = false;
        delayedSignal = options?.signal;
        return delayed;
      }
      return normal ? normal(input, options) : Promise.reject(new Error('Missing test handler.'));
    });
    const source = TestEventSource.instances.at(-1);
    await act(async () => {
      source?.open();
    });
    await waitFor(() => expect(delayedSignal).toBeTruthy());
    api.state.candidate = { ...candidate, status: 'Screening' };
    await act(async () => {
      source?.change();
    });
    await screen.findByText('Checking the evidence');
    expect(delayedSignal?.aborted).toBe(true);
    await act(async () => {
      finish?.(json(failed));
    });
    expect(screen.queryByText(/No evidence verdict was generated/)).toBeNull();
    expect(screen.getByText('Checking the evidence')).toBeTruthy();
    expect(
      api.fetcher.mock.calls.filter(([input]) => String(input) === `/api/candidates/${id}/result`),
    ).toHaveLength(0);
    api.state.candidate = { ...candidate, status: 'Completed' };
    api.state.notices = [
      {
        id: noticeId,
        candidateId: id,
        status: 'Completed',
        read: false,
        createdAt: candidate.createdAt,
      },
    ];
    await act(async () => {
      source?.change();
    });
    await screen.findByText('Built APIs with .NET.');
    expect(screen.getByText('Proposed evidence')).toBeTruthy();
    await waitFor(() =>
      expect(screen.getByRole('status').textContent).toContain('evidence is ready'),
    );
  });

  it('reuses the same key after an ambiguous failure and opens processing after durable acceptance', async () => {
    const api = fakeApi();
    api.submit.mockRejectedValueOnce(new TypeError('network failure'));
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );
    await fillSubmission(user);
    await user.click(screen.getByRole('button', { name: 'Submit for screening' }));
    await screen.findByText(/service could not be reached/i);
    await user.click(screen.getByRole('button', { name: 'Submit for screening' }));
    await screen.findByRole('heading', { name: candidate.displayName });
    expect(screen.getByRole('link', { name: /Start another submission/ })).toBeTruthy();
    const first = api.submit.mock.calls[0]?.[0];
    const second = api.submit.mock.calls[1]?.[0];
    expect(first?.headers).toEqual(second?.headers);
    expect(first?.body).toEqual(second?.body);
    expect(api.submit).toHaveBeenCalledTimes(2);
  });

  it('refreshes a rotated CSRF token once without losing input or resetting the owner', async () => {
    const api = fakeApi();
    api.submit.mockImplementationOnce(async () => {
      api.state.csrf = 'synthetic-rotated-csrf';
      return json({ error: { code: 'csrf_failed', message: 'Refresh token.' } }, 403);
    });
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    );
    await fillSubmission(user);
    await user.click(screen.getByRole('button', { name: 'Submit for screening' }));
    await screen.findByRole('heading', { name: candidate.displayName });
    expect(api.submit).toHaveBeenCalledTimes(2);
    const firstHeaders = api.submit.mock.calls[0]?.[0].headers;
    const secondHeaders = api.submit.mock.calls[1]?.[0].headers;
    expect(firstHeaders).toMatchObject({ 'X-CSRF-TOKEN': 'synthetic-csrf-a' });
    expect(secondHeaders).toMatchObject({ 'X-CSRF-TOKEN': 'synthetic-rotated-csrf' });
    expect(firstHeaders).toMatchObject({
      'Idempotency-Key': new Headers(secondHeaders).get('Idempotency-Key'),
    });
    expect(api.fetcher.mock.calls.some(([, options]) => options?.method === 'DELETE')).toBe(false);
  });

  it('preserves a second form when a previous job completes off-route', async () => {
    const api = fakeApi();
    api.state.candidate = { ...candidate };
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={[`/processing/${id}`]}>
        <App />
      </MemoryRouter>,
    );
    await screen.findByRole('heading', { name: candidate.displayName });
    const source = TestEventSource.instances.at(-1);
    await act(async () => {
      source?.open();
    });
    await user.click(screen.getByRole('link', { name: /Start another submission/ }));
    await user.type(screen.getByLabelText('Candidate profile'), 'Keep this unsaved text.');
    api.state.candidate = { ...candidate, status: 'Completed' };
    api.state.notices = [
      {
        id: noticeId,
        candidateId: id,
        status: 'Completed',
        read: false,
        createdAt: candidate.createdAt,
      },
    ];
    await act(async () => {
      source?.change();
    });
    await waitFor(() =>
      expect(screen.getByRole('status').textContent).toContain('evidence is ready'),
    );
    expect(screen.getByRole('heading', { name: 'New submission' })).toBeTruthy();
    expect((screen.getByLabelText('Candidate profile') as HTMLTextAreaElement).value).toBe(
      'Keep this unsaved text.',
    );
    expect(document.activeElement).toBe(screen.getByLabelText('Candidate profile'));
    await user.click(screen.getByRole('link', { name: /^Inbox/ }));
    await user.click(screen.getByRole('link', { name: 'Open' }));
    await screen.findByText('Built APIs with .NET.');
    expect(screen.getByText('Proposed evidence')).toBeTruthy();
  });

  it('clears owned state and closes the old stream after an explicit session reset', async () => {
    const api = fakeApi();
    api.state.candidate = { ...candidate, status: 'Completed' };
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={[`/results/${id}`]}>
        <App />
      </MemoryRouter>,
    );
    await screen.findByText('Built APIs with .NET.');
    const oldSource = TestEventSource.instances.at(-1);
    await user.click(screen.getByText('Browser session'));
    await user.click(screen.getByRole('button', { name: 'Start a new session' }));
    await screen.findByRole('heading', { name: 'New submission' });
    expect(oldSource?.closed).toBe(true);
    expect(screen.queryByText('Built APIs with .NET.')).toBeNull();
    await act(async () => {
      oldSource?.change();
    });
    expect(screen.queryByText(candidate.displayName)).toBeNull();
  });

  it('ignores a late result response after navigation to an unsaved form', async () => {
    const api = fakeApi();
    api.state.candidate = { ...candidate, status: 'Completed' };
    let finish: ((value: Response) => void) | undefined;
    const delayed = new Promise<Response>((resolve) => {
      finish = resolve;
    });
    const normal = api.fetcher.getMockImplementation();
    api.fetcher.mockImplementation((input, options) =>
      String(input) === `/api/candidates/${id}/result`
        ? delayed
        : normal
          ? normal(input, options)
          : Promise.reject(new Error('Missing test handler.')),
    );
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={[`/results/${id}`]}>
        <App />
      </MemoryRouter>,
    );
    await screen.findByRole('heading', { name: candidate.displayName });
    await user.click(screen.getByRole('link', { name: 'New submission' }));
    await user.type(screen.getByLabelText('Candidate profile'), 'A separate unsaved review.');
    await act(async () => {
      finish?.(
        json({
          candidateId: id,
          items: [
            {
              requirement: 'Build APIs',
              evidence: 'Late quote.',
              state: 'EvidenceFound',
            },
          ],
          reviewRequired: true,
          provider: 'Ollama',
          model: 'local-model',
        }),
      );
    });
    expect(screen.queryByText('Late quote.')).toBeNull();
    expect((screen.getByLabelText('Candidate profile') as HTMLTextAreaElement).value).toBe(
      'A separate unsaved review.',
    );
  });

  it('queues completion during a delayed snapshot and keeps the newer terminal status', async () => {
    const api = fakeApi();
    api.state.candidate = { ...candidate };
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/submissions']}>
        <App />
      </MemoryRouter>,
    );
    await screen.findByText(candidate.displayName);
    let finish: ((value: Response) => void) | undefined;
    let snapshotStarted = false;
    const delayed = new Promise<Response>((resolve) => {
      finish = resolve;
    });
    const normal = api.fetcher.getMockImplementation();
    api.fetcher.mockImplementation((input, options) => {
      if (String(input) === '/api/candidates' && options?.method === 'GET') {
        snapshotStarted = true;
        return delayed;
      }
      return normal ? normal(input, options) : Promise.reject(new Error('Missing test handler.'));
    });
    const source = TestEventSource.instances.at(-1);
    await act(async () => {
      source?.open();
    });
    await waitFor(() => expect(snapshotStarted).toBe(true));
    api.state.candidate = { ...candidate, status: 'Completed' };
    api.state.notices = [
      {
        id: noticeId,
        candidateId: id,
        status: 'Completed',
        read: false,
        createdAt: candidate.createdAt,
      },
    ];
    await act(async () => {
      source?.change();
    });
    expect(
      api.fetcher.mock.calls.filter(([input]) => String(input) === `/api/candidates/${id}`),
    ).toHaveLength(0);
    await act(async () => {
      finish?.(json({ items: [candidate], nextCursor: null }));
    });
    await screen.findByText('Ready for review');
    expect(screen.queryByText('Queued')).toBeNull();
    await user.click(screen.getByRole('link', { name: /^Inbox/ }));
    expect(screen.getByText('Evidence is ready for review')).toBeTruthy();
  });

  it('ignores a delayed page when a later page selection has already completed', async () => {
    const api = fakeApi();
    api.state.candidate = { ...candidate };
    api.state.candidateCursor = 'page-two';
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={['/submissions']}>
        <App />
      </MemoryRouter>,
    );
    await screen.findByText(candidate.displayName);
    let finish: ((value: Response) => void) | undefined;
    const delayed = new Promise<Response>((resolve) => {
      finish = resolve;
    });
    const normal = api.fetcher.getMockImplementation();
    api.fetcher.mockImplementation((input, options) =>
      String(input).includes('?cursor=')
        ? delayed
        : normal
          ? normal(input, options)
          : Promise.reject(new Error('Missing test handler.')),
    );
    await user.click(screen.getByRole('button', { name: /Next page/ }));
    api.state.candidate = { ...candidate, displayName: 'Latest synthetic record' };
    api.state.candidateCursor = null;
    await user.click(screen.getByRole('button', { name: 'Latest submissions' }));
    await screen.findByText('Latest synthetic record');
    await act(async () => {
      finish?.(
        json({ items: [{ ...candidate, displayName: 'Older snapshot' }], nextCursor: null }),
      );
    });
    expect(screen.queryByText('Older snapshot')).toBeNull();
    expect(screen.getByText('Latest synthetic record')).toBeTruthy();
  });

  it('hides result content after accepted owner deletion', async () => {
    const api = fakeApi();
    api.state.candidate = { ...candidate, status: 'Completed' };
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={[`/results/${id}`]}>
        <App />
      </MemoryRouter>,
    );
    await screen.findByText('Built APIs with .NET.');
    await user.click(screen.getByText('Delete submission'));
    await user.click(screen.getByRole('button', { name: 'Confirm deletion' }));
    await screen.findByRole('heading', { name: 'Worklist' });
    expect(screen.queryByText('Built APIs with .NET.')).toBeNull();
    expect(api.state.candidate).toBeNull();
  });
});
