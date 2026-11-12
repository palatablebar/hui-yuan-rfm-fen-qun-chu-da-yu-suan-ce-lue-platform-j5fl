from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config.database import Base
from app.constants.order_types import ORDER_TYPE_SALE


class OrderFact(Base):
    """POS 订单事实（核心实体 2）；金额单位为元。

    事件溯源式事实表：销售单 / 迟到补传单 / 退款单 / 撤销单都作为不可变事件落库，
    不覆盖、不删除原单。更正单（REFUND / VOID）通过 ``original_order_no`` 引用原单，
    ``correction_no`` 是门店侧更正投递的幂等键：同一更正重复投递只生效一次。
    """

    __tablename__ = "order_facts"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id"), index=True)
    store_code: Mapped[str] = mapped_column(String(12))
    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    ordered_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), index=True)
    coupon_issued: Mapped[bool] = mapped_column(Boolean, default=False)
    coupon_redeemed: Mapped[bool] = mapped_column(Boolean, default=False)
    coupon_discount: Mapped[float] = mapped_column(Numeric(8, 2), default=0)
    # SALE / LATE_SALE / REFUND / VOID；历史行与未显式指定类型的补传单默认 SALE
    order_type: Mapped[str] = mapped_column(String(16), default=ORDER_TYPE_SALE, index=True)
    # 更正单引用的原销售单号（REFUND / VOID 必填）；销售单为 NULL
    original_order_no: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    # 门店 POS 更正投递幂等键（更正单必填，全局唯一）
    correction_no: Mapped[str | None] = mapped_column(String(32), nullable=True, unique=True, index=True)
    # POS 补传/更正到达系统的时间，区别于业务发生时间 ordered_at
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    member = relationship("Member", lazy="joined")
