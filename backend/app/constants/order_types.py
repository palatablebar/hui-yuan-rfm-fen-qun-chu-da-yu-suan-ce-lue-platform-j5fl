"""POS 订单 / 更正事件类型（前后端各一份，前端见 constants/orderTypes.ts）。

门店 POS 会补传三类数据：
- ``SALE``：正常销售订单（order_facts 中历史数据默认即此类型）；
- ``REFUND``：退款单，金额为负向冲减，引用原销售单，冲减 GMV（不冲减订单数）；
- ``VOID``：撤销单，引用原销售单，原单整笔作废（同时冲减订单数与 GMV）；
- ``LATE_SALE``：迟到补传的销售单，本质仍是销售，是否计入取决于业务发生时点
  与统计日（as-of）的关系，类型单独保留仅为对账可追溯。
"""

ORDER_TYPE_SALE = "SALE"
ORDER_TYPE_REFUND = "REFUND"
ORDER_TYPE_VOID = "VOID"
ORDER_TYPE_LATE_SALE = "LATE_SALE"

# 更正类事件：必须引用原销售单，同一更正单重复投递只生效一次
CORRECTION_TYPES = (ORDER_TYPE_REFUND, ORDER_TYPE_VOID)
# 销售类事件：正常单与迟到补传单
SALE_TYPES = (ORDER_TYPE_SALE, ORDER_TYPE_LATE_SALE)
ALL_ORDER_TYPES = SALE_TYPES + CORRECTION_TYPES

ORDER_TYPE_META = {
    ORDER_TYPE_SALE: {"label": "销售单", "correction": False},
    ORDER_TYPE_LATE_SALE: {"label": "迟到补传单", "correction": False},
    ORDER_TYPE_REFUND: {"label": "退款单", "correction": True},
    ORDER_TYPE_VOID: {"label": "撤销单", "correction": True},
}
