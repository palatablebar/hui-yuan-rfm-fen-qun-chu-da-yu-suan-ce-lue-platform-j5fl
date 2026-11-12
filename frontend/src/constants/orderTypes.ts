// POS 订单 / 更正事件类型，与后端 app/constants/order_types.py 同值镜像
export const ORDER_TYPE_SALE = 'SALE'
export const ORDER_TYPE_LATE_SALE = 'LATE_SALE'
export const ORDER_TYPE_REFUND = 'REFUND'
export const ORDER_TYPE_VOID = 'VOID'

export type OrderType = 'SALE' | 'LATE_SALE' | 'REFUND' | 'VOID'

export const ORDER_TYPE_META: Record<OrderType, { label: string; correction: boolean; tone: 'ok' | 'muted' | 'warn' | 'danger' }> = {
  SALE: { label: '销售单', correction: false, tone: 'ok' },
  LATE_SALE: { label: '迟到补传单', correction: false, tone: 'muted' },
  REFUND: { label: '退款单', correction: true, tone: 'warn' },
  VOID: { label: '撤销单', correction: true, tone: 'danger' },
}

// 原单在 as-of 台账中的状态
export const ORDER_STATUS_META: Record<string, string> = {
  effective: '有效',
  refunded: '部分退款',
  fully_refunded: '全额退款',
  voided: '已撤销',
  after_cutoff: '统计日后',
}

// 更正未采纳原因
export const NOT_APPLIED_REASON_META: Record<string, string> = {
  duplicate_correction_no: '重复投递（幂等拦截）',
  after_cutoff: '统计日之后，未采纳',
  original_order_missing: '原单不存在',
  original_after_cutoff: '原单在统计日之后，无对象可冲减',
  original_already_voided: '原单已撤销',
}
