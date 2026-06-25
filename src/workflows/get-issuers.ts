import { webhook, fn, http } from '@versori/run';
import type { GetIssuersVariables, PaginatedIssuersResult } from '../types/issuers';

/**
 * Sync webhook — acts as a transparent proxy to the Avalara 1099 ListIssuers endpoint.
 *
 * Required integration variables (set in the platform UI):
 *   • bearerToken   — OAuth 2.0 bearer token supplied by the end user / calling system
 *   • taxYear       — Tax year to filter issuers by (e.g. 2024)
 *   • xCorrelationId — Unique GUID for request tracing / audit
 *
 * The caller receives the raw Avalara paginated issuer list as the HTTP response.
 */
export const getIssuersWebhook = webhook('get-issuers', {
  response: { mode: 'sync' },
  cors: true,
})
  // ── Step 1: Read & validate integration variables ─────────────────────────
  .then(
    fn('read-variables', ({ activation, log }) => {
      const bearerToken = activation.getVariable('bearerToken') as string | undefined;
      const taxYear = activation.getVariable('taxYear') as number | undefined;
      const xCorrelationId = activation.getVariable('xCorrelationId') as string | undefined;

      if (!bearerToken) {
        throw new Error('Integration variable "bearerToken" is required but not set.');
      }
      if (!taxYear) {
        throw new Error('Integration variable "taxYear" is required but not set.');
      }
      if (!xCorrelationId) {
        throw new Error('Integration variable "xCorrelationId" is required but not set.');
      }

      log.info('GetIssuers — variables resolved', { taxYear, xCorrelationId });

      return { bearerToken, taxYear, xCorrelationId } satisfies GetIssuersVariables;
    })
  )
  // ── Step 2: Call Avalara 1099 ListIssuers ─────────────────────────────────
  .then(
    http(
      'call-avalara-get-issuers',
      { connection: 'avalara_1099_api' },
      async ({ fetch, data, log }): Promise<PaginatedIssuersResult> => {
        const { bearerToken, taxYear, xCorrelationId } = data as GetIssuersVariables;

        // taxYear is a filterable field on /1099/issuers
        // ref: https://github.com/avadev/Avalara-SDK-DotNet/blob/main/docs/A1099/V2/Class1099IssuersApi.md
        const params = new URLSearchParams({
          $filter: `taxYear eq ${taxYear}`,
          $count: 'true',
        });

        log.info('Calling Avalara 1099 ListIssuers', {
          taxYear,
          xCorrelationId,
          filter: params.get('$filter'),
        });

        const response = await fetch(`/avalara1099/1099/issuers?${params.toString()}`, {
          method: 'GET',
          headers: {
            Authorization: `Bearer ${bearerToken}`,
            'avalara-version': '2.0',
            'X-Correlation-Id': xCorrelationId,
            Accept: 'application/json',
          },
        });

        if (!response.ok) {
          const errorBody = await response.text();
          log.error('Avalara ListIssuers request failed', {
            status: response.status,
            statusText: response.statusText,
            body: errorBody,
          });
          throw new Error(
            `Avalara GetIssuers failed: ${response.status} ${response.statusText} — ${errorBody}`
          );
        }

        const result: PaginatedIssuersResult = await response.json();

        log.info('Avalara GetIssuers succeeded', {
          recordSetCount: result['@recordSetCount'] ?? 'n/a',
          returnedCount: result.value?.length ?? 0,
        });

        return result;
      }
    )
  )
  // ── Error handler ─────────────────────────────────────────────────────────
  .catch((ctx) => {
    ctx.log.error('GetIssuers webhook failed', { error: String(ctx.data) });
  });
