export interface OrderStoreStat {
  store_code: string
  store_name: string
  orders: number
  gmv: number
  coupon_issued: number
  coupon_redeemed: number
  redemption_rate: number
  /** 事件表原始条数（含退款/撤销/迟到事件），用于与有效口径对照 */
  raw_events: number
  raw_amount_sum: number
}

export interface OrderTotals {
  orders: number
  gmv: number
  coupon_issued: number
  coupon_redeemed: number
  redemption_rate: number
}

export interface CorrectionOverview {
  sale_orders: number
  effective_orders: number
  late_sale_orders: number
  after_cutoff_sales: number
  voided_orders: number
  refunded_orders: number
  gross_gmv: number
  refund_amount: number
  void_amount: number
  net_gmv: number
  correction_total: number
  correction_applied: number
  correction_duplicate_total: number
}

export interface OrderSummary {
  as_of: string
  totals: OrderTotals
  stores: OrderStoreStat[]
  correction_overview: CorrectionOverview
}
