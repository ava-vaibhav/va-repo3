// ─── Avalara 1099 Issuers – shared type definitions ────────────────────────

export interface GetIssuersVariables {
  bearerToken: string;
  taxYear: number;
  xCorrelationId: string;
}

export interface IssuerModel {
  id: string;
  name: string;
  referenceId?: string | null;
  taxYear: number;
  tin?: string | null;
  address?: string | null;
  address2?: string | null;
  city?: string | null;
  state?: string | null;
  zip?: string | null;
  countryCode?: string | null;
  email?: string | null;
  phone?: string | null;
}

export interface PaginatedIssuersResult {
  value: IssuerModel[];
  '@recordSetCount'?: number;
  '@nextLink'?: string | null;
}
