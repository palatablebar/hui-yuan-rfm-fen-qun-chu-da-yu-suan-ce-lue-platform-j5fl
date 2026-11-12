import type { OrderSummary } from '../types/order'
import type {
  CorrectionIngestPayload,
  CorrectionIngestResult,
  CorrectionStream,
  Reconciliation,
} from '../types/reconciliation'
import { http } from './http'

export function fetchOrderSummary(asOf?: string): Promise<OrderSummary> {
  const query = asOf ? `?as_of=${asOf}` : ''
  return http.get<OrderSummary>(`/orders/summary${query}`)
}

export function ingestCorrection(payload: CorrectionIngestPayload): Promise<CorrectionIngestResult> {
  return http.post<CorrectionIngestResult>('/orders/corrections', payload)
}

export function fetchReconciliation(params: {
  current_run_id?: number
  baseline_run_id?: number
  total_budget?: number
}): Promise<Reconciliation> {
  const query = new URLSearchParams()
  if (params.current_run_id) query.set('current_run_id', String(params.current_run_id))
  if (params.baseline_run_id) query.set('baseline_run_id', String(params.baseline_run_id))
  if (params.total_budget) query.set('total_budget', String(params.total_budget))
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return http.get<Reconciliation>(`/reconciliation${suffix}`)
}

export function fetchCorrectionStream(params: { as_of?: string; kind?: string }): Promise<CorrectionStream> {
  const query = new URLSearchParams()
  if (params.as_of) query.set('as_of', params.as_of)
  if (params.kind) query.set('kind', params.kind)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return http.get<CorrectionStream>(`/reconciliation/corrections${suffix}`)
}
