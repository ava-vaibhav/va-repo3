export interface GetIssuersRequest {
  bearerToken: string;
  taxYear: number;
  xCorrelationId: string;
}

export interface IssuerModel {
  id?: string;
  name?: string;
  tin?: string;
  taxYear?: number;
  [key: string]: unknown;
}

export interface PaginatedIssuersResponse {
  value?: IssuerModel[];
  count?: number;
  nextLink?: string;
  [key: string]: unknown;
}
