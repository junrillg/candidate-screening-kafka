export type Status = 'Queued' | 'Screening' | 'Completed' | 'Failed';
export type Session = { ownerId: string; csrfToken: string };
export type Candidate = {
  id: string;
  displayName: string;
  status: Status;
  createdAt: string;
  expiresAt: string;
  errorCode: string | null;
};
export type Accepted = Omit<Candidate, 'errorCode'>;
export type Submission = { displayName: string; profile: string; requirements: string[] };
export type Notice = {
  id: string;
  candidateId: string;
  status: 'Completed' | 'Failed';
  read: boolean;
  createdAt: string;
};
export type EvidenceResult = {
  candidateId: string;
  items: {
    requirement: string;
    evidence: string | null;
    state: 'EvidenceFound' | 'NeedsReview' | 'NotEvidenced';
  }[];
  reviewRequired: true;
  provider: string;
  model: string;
};
export type Page<T> = { items: T[]; nextCursor: string | null };

const messages: Record<string, string> = {
  validation_failed: 'Check the highlighted input and try again.',
  csrf_failed: 'This session needs to be refreshed. Refresh the session and try again.',
  session_expired: 'This browser session has changed. Refresh it to continue.',
  not_found:
    'This item is unavailable. It may have expired, been deleted, or belong to another session.',
  idempotency_conflict:
    'This retry no longer matches the original submission. Edit the input before submitting again.',
  result_not_ready: 'The evidence is not ready yet. Check the processing status.',
  rate_limited: 'The service is busy. Wait a moment, then try again.',
  inference_failed: 'Screening could not produce a reliable result. You can submit a new review.',
  internal_error: 'The service could not complete this request. Try again shortly.',
  invalid_response: 'The service returned an unexpected response. Retry or refresh this session.',
  connection_failed: 'The service could not be reached. Check your connection, then retry.',
  request_timeout:
    'Acceptance could not be confirmed in time. Retry the same input to check it safely.',
};

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly fields: Record<string, string> = {},
  ) {
    super(messages[code] ?? messages.internal_error);
    this.name = 'ApiError';
  }
}

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new ApiError(0, 'invalid_response');
  }
  return value as Record<string, unknown>;
}
function text(value: unknown, max: number): string {
  if (typeof value !== 'string' || value.length < 1 || value.length > max) {
    throw new ApiError(0, 'invalid_response');
  }
  return value;
}
export function parseId(value: unknown): string {
  const id = text(value, 36);
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) {
    throw new ApiError(0, 'invalid_response');
  }
  return id;
}
function date(value: unknown): string {
  const result = text(value, 40);
  if (!Number.isFinite(Date.parse(result))) throw new ApiError(0, 'invalid_response');
  return result;
}
function status(value: unknown): Status {
  if (value !== 'Queued' && value !== 'Screening' && value !== 'Completed' && value !== 'Failed') {
    throw new ApiError(0, 'invalid_response');
  }
  return value;
}
export function parseSession(value: unknown): Session {
  const item = object(value);
  return { ownerId: parseId(item.ownerId), csrfToken: text(item.csrfToken, 4096) };
}
export function parseAccepted(value: unknown): Accepted {
  const item = object(value);
  return {
    id: parseId(item.id),
    displayName: text(item.displayName, 80),
    status: status(item.status),
    createdAt: date(item.createdAt),
    expiresAt: date(item.expiresAt),
  };
}
export function parseCandidate(value: unknown): Candidate {
  const item = object(value);
  return {
    ...parseAccepted(item),
    errorCode: item.errorCode === null ? null : text(item.errorCode, 80),
  };
}
export function parseNotice(value: unknown): Notice {
  const item = object(value);
  if ((item.status !== 'Completed' && item.status !== 'Failed') || typeof item.read !== 'boolean') {
    throw new ApiError(0, 'invalid_response');
  }
  return {
    id: parseId(item.id),
    candidateId: parseId(item.candidateId),
    status: item.status,
    read: item.read,
    createdAt: date(item.createdAt),
  };
}
export function parsePage<T>(parse: (value: unknown) => T): (value: unknown) => Page<T> {
  return (value) => {
    const page = object(value);
    if (!Array.isArray(page.items) || page.items.length > 100) {
      throw new ApiError(0, 'invalid_response');
    }
    return {
      items: page.items.map(parse),
      nextCursor: page.nextCursor === null ? null : text(page.nextCursor, 2048),
    };
  };
}
export function parseResult(value: unknown): EvidenceResult {
  const result = object(value);
  if (result.reviewRequired !== true || !Array.isArray(result.items) || result.items.length > 8) {
    throw new ApiError(0, 'invalid_response');
  }
  return {
    candidateId: parseId(result.candidateId),
    items: result.items.map((value: unknown) => {
      const item = object(value);
      if (
        item.state !== 'EvidenceFound' &&
        item.state !== 'NeedsReview' &&
        item.state !== 'NotEvidenced'
      ) {
        throw new ApiError(0, 'invalid_response');
      }
      return {
        requirement: text(item.requirement, 200),
        evidence: item.evidence === null ? null : text(item.evidence, 12000),
        state: item.state,
      };
    }),
    reviewRequired: true,
    provider: text(result.provider, 100),
    model: text(result.model, 200),
  };
}
export function parseChange(value: unknown): string {
  return parseId(object(value).candidateId);
}

async function readJson(response: Response): Promise<unknown> {
  if (!response.body) throw new ApiError(response.status, 'invalid_response');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let bytes = 0;
  let content = '';
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > 262144) {
        await reader.cancel();
        throw new ApiError(response.status, 'invalid_response');
      }
      content += decoder.decode(value, { stream: true });
    }
    const parsed: unknown = JSON.parse(content + decoder.decode());
    return parsed;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(response.status, 'invalid_response');
  } finally {
    reader.releaseLock();
  }
}

type Options = {
  method?: 'GET' | 'POST' | 'DELETE';
  body?: Submission;
  csrfToken?: string;
  key?: string;
  signal?: AbortSignal;
};
export async function request<T>(
  path: string,
  parse: (value: unknown) => T,
  options: Options = {},
): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 15000);
  const abort = () => controller.abort();
  options.signal?.addEventListener('abort', abort, { once: true });
  if (options.signal?.aborted) controller.abort();
  try {
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (options.body) headers['Content-Type'] = 'application/json';
    if (options.csrfToken) headers['X-CSRF-TOKEN'] = options.csrfToken;
    if (options.key) headers['Idempotency-Key'] = options.key;
    const response = await fetch(path, {
      method: options.method ?? 'GET',
      credentials: 'same-origin',
      cache: 'no-store',
      headers,
      ...(options.body ? { body: JSON.stringify(options.body) } : {}),
      signal: controller.signal,
    });
    if (!response.ok) {
      let code =
        response.status === 404 || response.status === 410
          ? 'not_found'
          : response.status === 401
            ? 'session_expired'
            : 'internal_error';
      const fields: Record<string, string> = {};
      try {
        const payload = object(await readJson(response));
        const error = object(payload.error);
        if (typeof error.code === 'string' && Object.hasOwn(messages, error.code))
          code = error.code;
        if (error.fields && typeof error.fields === 'object' && !Array.isArray(error.fields)) {
          const details = object(error.fields);
          for (const key of ['displayName', 'profile', 'requirements']) {
            if (typeof details[key] === 'string' && details[key].length <= 300)
              fields[key] = details[key];
          }
        }
      } catch {
        // Malformed error bodies never expose server content to the interface.
      }
      throw new ApiError(response.status, code, fields);
    }
    return parse(response.status === 204 ? null : await readJson(response));
  } catch (error) {
    if (options.signal?.aborted) throw new DOMException('Request cancelled.', 'AbortError');
    if (error instanceof ApiError) throw error;
    throw new ApiError(0, controller.signal.aborted ? 'request_timeout' : 'connection_failed');
  } finally {
    window.clearTimeout(timeout);
    options.signal?.removeEventListener('abort', abort);
  }
}
export const noContent = (value: unknown): undefined => {
  if (value !== null) throw new ApiError(0, 'invalid_response');
  return undefined;
};
export const candidatePage = parsePage(parseCandidate);
export const noticePage = parsePage(parseNotice);
export function isCancelled(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}
export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : (messages.internal_error ?? 'Request failed.');
}

export type RefreshSession = (fence: boolean) => Promise<Session | null>;
export async function mutate<T>(
  path: string,
  parse: (value: unknown) => T,
  options: Omit<Options, 'csrfToken'>,
  session: Session,
  refreshSession: RefreshSession,
): Promise<T> {
  try {
    return await request(path, parse, { ...options, csrfToken: session.csrfToken });
  } catch (error) {
    if (error instanceof ApiError && (error.status === 401 || error.code === 'session_expired')) {
      void refreshSession(true);
      throw new DOMException('Session changed.', 'AbortError');
    }
    if (!(error instanceof ApiError) || error.code !== 'csrf_failed') throw error;
    const current = await refreshSession(false);
    if (options.signal?.aborted) throw new DOMException('Request cancelled.', 'AbortError');
    if (!current) throw new ApiError(0, 'connection_failed');
    if (current.ownerId !== session.ownerId)
      throw new DOMException('Session changed.', 'AbortError');
    // An explicit CSRF rejection had no effect; preserve the original idempotency key.
    return request(path, parse, { ...options, csrfToken: current.csrfToken });
  }
}
