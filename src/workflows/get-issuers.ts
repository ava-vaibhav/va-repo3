import { webhook, fn, http } from '@versori/run';
import type { GetIssuersRequest, PaginatedIssuersResponse } from '../types/issuers';

interface ApiResponse<T = unknown> {
  success: boolean;
  status: number;
  data?: T;
  error?: string;
}

/**
 * Webhook: POST /webhooks/get-issuers
 *
 * Expected request body:
 * {
 *   "bearerToken":    "Bearer <token>",   // full value including 'Bearer ' prefix
 *   "taxYear":        2024,
 *   "xCorrelationId": "uuid-v4-string"
 * }
 *
 * The integration calls Avalara 1099 ListIssuers (GET /1099/issuers)
 * using the supplied bearer token, and returns the paginated issuers list
 * synchronously to the caller.
 */
export const getIssuersWebhook = webhook('get-issuers', {
  response: { mode: 'sync' },
})
  .then(
    fn('validate-input', ({ data, log }) => {
      const body = data?.body as Partial<GetIssuersRequest> | undefined;
			
			log.info("Request Body", body);
			
      if (!body?.bearerToken) {
        throw new Error('Missing required field: bearerToken');
      }
      if (body.taxYear === undefined || body.taxYear === null) {
        throw new Error('Missing required field: taxYear');
      }
      if (!body.xCorrelationId) {
        throw new Error('Missing required field: xCorrelationId');
      }

      log.info('Input validated', { taxYear: body.taxYear, xCorrelationId: body.xCorrelationId });

      return {
        bearerToken: body.bearerToken,
        taxYear: body.taxYear,
        xCorrelationId: body.xCorrelationId,
      } satisfies GetIssuersRequest;
    })
  )
  .then(
    http('call-avalara-get-issuers', { connection: 'avalara_1099' }, async ({ fetch, data, log }) => {
      const { bearerToken, taxYear, xCorrelationId } = data as GetIssuersRequest;

      // Build query: filter issuers by taxYear, pin API version to 2.0
      const params = new URLSearchParams({
        $filter: `taxYear eq ${taxYear}`,
      });

      log.info('Calling Avalara 1099 ListIssuers', { taxYear, xCorrelationId });

      const response = await fetch(`/1099/issuers?${params.toString()}`, {
        method: 'GET',
        headers: {
          // Override the connection-level auth with the caller-supplied bearer token.
          // The platform stores API-key secrets verbatim, so the caller must include
          // the 'Bearer ' prefix in bearerToken.
          Authorization: bearerToken,
          'avalara-version': '2.0',
          'X-Correlation-Id': xCorrelationId,
          Accept: 'application/json',
        },
      });

      if (!response.ok) {
        const errorBody = await response.text();
        log.error('Avalara API error', { status: response.status, body: errorBody });

        // Return a structured error envelope — do NOT throw, so the sync webhook
        // delivers this payload to the caller instead of an uncontrolled failure.
        return {
          success: false,
          status: response.status,
          error: errorBody,
        } satisfies ApiResponse;
      }

      const result: PaginatedIssuersResponse = await response.json();
      log.info('ListIssuers succeeded', { count: result.count ?? result.value?.length ?? 0 });

      // Return a structured success envelope.
      return {
        success: true,
        status: response.status,
        data: result,
      } satisfies ApiResponse<PaginatedIssuersResponse>;
    })
  )
  .catch((ctx) => {
    // Catches validation errors and any unexpected failures.
    // Return a structured envelope so the caller always receives consistent JSON.
    const message = ctx.data instanceof Error ? ctx.data.message : String(ctx.data);
    ctx.log.error('get-issuers workflow failed', { error: message });
    return {
      success: false,
      status: 500,
      error: message,
    } satisfies ApiResponse;
  });
