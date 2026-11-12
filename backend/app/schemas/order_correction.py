from datetime import datetime

from pydantic import BaseModel, Field

from app.constants.order_types import ALL_ORDER_TYPES, CORRECTION_TYPES, SALE_TYPES


class CorrectionIngestRequest(BaseModel):
    """门店 POS 补传事件（销售/迟到单/退款/撤销统一入口）。"""

    order_no: str = Field(min_length=1, max_length=24)
    correction_no: str | None = Field(default=None, max_length=32)
    order_type: str = Field(default="SALE")
    original_order_no: str | None = Field(default=None, max_length=24)
    member_no: str = Field(min_length=1, max_length=24)
    store_code: str = Field(min_length=1, max_length=12)
    amount: float = Field(ge=0)
    ordered_at: datetime
    coupon_issued: bool = False
    coupon_redeemed: bool = False
    coupon_discount: float = Field(default=0.0, ge=0)

    def validate_rules(self) -> str | None:
        if self.order_type not in ALL_ORDER_TYPES:
            return f"order_type 只能是 {'/'.join(ALL_ORDER_TYPES)}"
        if self.order_type in CORRECTION_TYPES:
            if not self.original_order_no:
                return f"{self.order_type} 更正单必须提供 original_order_no"
            if not self.correction_no:
                return f"{self.order_type} 更正单必须提供 correction_no（幂等键）"
        if self.order_type in SALE_TYPES and self.original_order_no:
            return "销售类事件不能携带 original_order_no"
        return None
