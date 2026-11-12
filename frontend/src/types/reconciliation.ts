export interface OrderSaleRow {
  order_no: string
  member_id: number
  store_code: string
  order_type: string
  ordered_at: string
  gross_amount: number
  refunded_amount: number
  net_amount: number
  voided: boolean
  void_correction_no: string | null
  within_cutoff: boolean
  status: string
}

export interface OrderCorrectionRow {
  order_no: string
  correction_no: string
  order_type: string
  original_order_no: string | null
  member_id: number
  reversal_amount: number
  ordered_at: string
  within_cutoff: boolean
  applied: boolean
  duplicate: boolean
  not_applied_reason: string
}

export interface OrderLedgerSummary {
  sale_orders: number
  late_sale_orders: number
  after_cutoff_sales: number
  voided_orders: number
  refunded_orders: number
  effective_orders: number
  gross_gmv: number
  refund_amount: number
  void_amount: number
  net_gmv: number
  correction_total: number
  correction_applied: number
  correction_refund_total: number
  correction_void_total: number
  correction_duplicate_total: number
  correction_after_cutoff_total: number
}

export interface OrderReconciliation {
  sales: OrderSaleRow[]
  corrections: OrderCorrectionRow[]
  summary: OrderLedgerSummary
}

export interface MemberConsumptionRow {
  member_id: number
  member_no: string
  name: string
  store_code: string
  store_name: string
  order_count: number
  effective_order_count: number
  gross_amount: number
  refund_amount: number
  void_amount: number
  net_amount: number
  current_cluster: string | null
  baseline_cluster: string | null
  cluster_changed: boolean
}

export interface ClusterDifference {
  cluster_key: string
  label: string
  kind: string
  current_member_count: number
  baseline_member_count: number
  member_count_delta: number
  current_reachable_count: number
  baseline_reachable_count: number
  reachable_delta: number
  current_gmv: number
  baseline_gmv: number
  gmv_delta: number
  current_redemption_rate: number
  baseline_redemption_rate: number
  present_in_both: boolean
}

export interface BudgetDifference {
  cluster_key: string
  total_budget: number
  current_budget: number
  baseline_budget: number
  budget_delta: number
  current_coupon_face: number | null
  baseline_coupon_face: number | null
  current_expected_redemptions: number
  baseline_expected_redemptions: number
  current_expected_gmv: number
  baseline_expected_gmv: number
  expected_gmv_delta: number
  current_roi: number
  baseline_roi: number
}

export interface BudgetDifferences {
  total_budget: number
  current_totals: Record<string, number> | null
  baseline_totals: Record<string, number> | null
  allocations: BudgetDifference[]
}

export interface ReconciliationRun {
  id: number
  run_no: string
  as_of: string
  selected_k: number | null
  member_count: number | null
  gmv_total: number | null
  gross_gmv_total?: number | null
  refund_gmv_total?: number | null
}

export interface Reconciliation {
  as_of: string
  current_run: ReconciliationRun
  baseline_run: ReconciliationRun | null
  consistency: {
    snapshot_net_gmv: number | null
    ledger_net_gmv: number
    net_gmv_matches: boolean
  }
  order_reconciliation: OrderReconciliation
  member_consumption: MemberConsumptionRow[]
  cluster_differences: ClusterDifference[]
  budget_differences: BudgetDifferences
}

export interface CorrectionStreamItem {
  order_no: string
  correction_no: string
  order_type: string
  original_order_no: string | null
  member_no: string | null
  member_name: string | null
  store_code: string
  reversal_amount: number
  ordered_at: string
  received_at: string | null
}

export interface CorrectionStream {
  items: CorrectionStreamItem[]
  total: number
}

export interface CorrectionIngestPayload {
  order_no: string
  correction_no?: string | null
  order_type: string
  original_order_no?: string | null
  member_no: string
  store_code: string
  amount: number
  ordered_at: string
  coupon_issued?: boolean
  coupon_redeemed?: boolean
  coupon_discount?: number
}

export interface CorrectionIngestResult {
  item: {
    order_no: string
    correction_no: string | null
    order_type: string
    original_order_no: string | null
    amount: number
    ordered_at: string
    duplicate: boolean
  }
  duplicate: boolean
}
