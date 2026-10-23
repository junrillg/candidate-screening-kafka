import { describe, expect, it } from 'vitest';
import { parseCandidate, parseResult, parseSession } from './api';

const id = '11111111-1111-4111-8111-111111111111';
describe('API trust boundaries', () => {
  it('rejects malformed identity and unrecognized status', () => {
    expect(() => parseSession({ ownerId: '../other', csrfToken: 'token' })).toThrow();
    expect(() =>
      parseCandidate({
        id,
        displayName: 'Synthetic',
        status: 'Hired',
        createdAt: '2026-10-20T10:00:00Z',
        expiresAt: '2026-10-27T10:00:00Z',
        errorCode: null,
      }),
    ).toThrow();
  });
  it('rejects a provider result without mandatory human review', () => {
    expect(() =>
      parseResult({
        candidateId: id,
        items: [],
        reviewRequired: false,
        provider: 'Ollama',
        model: 'model',
      }),
    ).toThrow();
  });
  it('keeps evidence as plain text without an HTML rendering path', () => {
    const result = parseResult({
      candidateId: id,
      items: [
        {
          requirement: 'Review',
          evidence: '<script>untrusted</script>',
          state: 'NeedsReview',
        },
      ],
      reviewRequired: true,
      provider: 'Ollama',
      model: 'model',
    });
    expect(result.items[0]?.evidence).toBe('<script>untrusted</script>');
  });
});
