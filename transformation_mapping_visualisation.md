# Transformation Mapping — GetIssuers Integration

**Last updated:** 2026-06-25  
**Source system:** Versori Integration Variables (caller-supplied)  
**Target system:** Avalara 1099 API — `GET /avalara1099/1099/issuers`

---

## Overview

This integration exposes a sync webhook endpoint. The calling system passes three inputs as integration variables. The integration validates them, constructs an authenticated Avalara API request, and proxies the paginated issuer list straight back to the caller.

```
Caller → [Webhook] → [Read & Validate Variables] → [Avalara ListIssuers API] → Caller (response)
```

---

## 1. Integration Inputs

These values must be configured as **Integration Variables** in the Versori platform UI before the webhook can be invoked.

| Variable | Type | Required | Description |
|---|---|---|---|
| `bearerToken` | `string` | ✅ Yes | OAuth 2.0 Bearer token that authenticates the request to Avalara |
| `taxYear` | `number` | ✅ Yes | Calendar year to filter issuers by (e.g. `2024`) |
| `xCorrelationId` | `string` | ✅ Yes | GUID used for end-to-end request tracing and audit |

---

## 2. Variable → Avalara Request Mapping

The table below shows how each integration variable is placed in the outbound Avalara HTTP request.

| Integration Variable | Maps To | Location in Avalara Request | Example Value |
|---|---|---|---|
| `bearerToken` | `Authorization` header | HTTP Header | `Bearer eyJhbGc...` |
| `taxYear` | `$filter` OData query param | Query String | `$filter=taxYear eq 2024` |
| `xCorrelationId` | `X-Correlation-Id` header | HTTP Header | `fc881b6b-f990-459c-85e8-4c9cb4ff6ea5` |

**Fixed / hard-coded request values** (not configurable):

| Field | Value | Purpose |
|---|---|---|
| `avalara-version` header | `2.0` | Pins the Avalara API version |
| `$count` query param | `true` | Requests a total record count in the response envelope |
| `Accept` header | `application/json` | Enforces JSON response format |
| HTTP method | `GET` | Read-only; no state is mutated on Avalara |

---

## 3. Avalara Response → Caller Response Mapping

The Avalara response is returned **as-is** to the calling system — no fields are added, removed, or renamed.

### Response Envelope

| Avalara Response Field | Type | Description |
|---|---|---|
| `value` | `IssuerModel[]` | Array of issuer records matching the taxYear filter |
| `@recordSetCount` | `number` (optional) | Total number of records available (before pagination) |
| `@nextLink` | `string \| null` (optional) | OData next-page URL; `null` or absent when no further pages exist |

### IssuerModel Fields

| Field | Type | Notes |
|---|---|---|
| `id` | `string` | Avalara-assigned issuer identifier |
| `name` | `string` | Legal name of the issuing entity |
| `referenceId` | `string \| null` | Optional caller-supplied external reference |
| `taxYear` | `number` | Tax year of this issuer record (mirrors the filter) |
| `tin` | `string \| null` | Taxpayer Identification Number |
| `address` | `string \| null` | Street address line 1 |
| `address2` | `string \| null` | Street address line 2 |
| `city` | `string \| null` | City |
| `state` | `string \| null` | State / province code |
| `zip` | `string \| null` | Postal / ZIP code |
| `countryCode` | `string \| null` | ISO 3166-1 alpha-2 country code |
| `email` | `string \| null` | Contact e-mail for the issuer |
| `phone` | `string \| null` | Contact phone number for the issuer |

---

## 4. Validation Rules

| Rule | Behaviour on Failure |
|---|---|
| `bearerToken` must be a non-empty string | Workflow aborts before any API call; caller receives a 500 error response |
| `taxYear` must be a truthy number | Workflow aborts before any API call; caller receives a 500 error response |
| `xCorrelationId` must be a non-empty string | Workflow aborts before any API call; caller receives a 500 error response |
| Avalara HTTP response must be 2xx | Full Avalara error status + body is logged and surfaced to the caller |

---

## 5. Error Handling Notes

- **Variable validation** happens in the `read-variables` step — failure here means **no outbound network call** is ever made to Avalara, preventing unnecessary token usage.
- **Avalara API errors** (4xx / 5xx) are captured via `response.ok`, the full response body text is logged at `error` level, and the error message includes the HTTP status code plus status text to aid debugging.
- The `.catch()` handler at the workflow level acts as a final safety net, logging any uncaught errors so they appear in the platform's execution audit log even if the error propagates unexpectedly.
- Because the webhook runs in **sync mode**, any thrown error automatically causes the platform to return a non-200 HTTP response to the original caller.
