"""门店 POS 补传事件落库：退款 / 撤销 / 迟到订单的幂等写入。

设计要点：
- ``order_facts`` 是只增不改的事件表，原单永不被覆盖；更正以独立事件引用原单；
- ``correction_no`` 是门店侧幂等键（DB 唯一索引兜底）：同一笔更正重复投递，
  第二次起直接回首次结果，不再产生新行、不二次冲减；
- 退款 / 撤销金额按负值落库（冲减方向显式化），销售类事件按正值落库；
- 业务合法性在写入前校验（原单存在、属于同一会员、未被撤销、退款不超原单金额）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.constants.error_codes import AppError, ErrorCode
from app.constants.order_types import (
    ORDER_TYPE_REFUND,
    ORDER_TYPE_VOID,
    SALE_TYPES,
)
from app.models import Member, OrderFact


def _serialize(order: OrderFact, *, duplicate: bool = False) -> dict:
    return {
        "order_no": order.order_no,
        "correction_no": order.correction_no,
        "order_type": order.order_type,
        "original_order_no": order.original_order_no,
        "member_id": order.member_id,
        "store_code": order.store_code,
        "amount": float(order.amount),
        "ordered_at": order.ordered_at.isoformat(),
        "duplicate": duplicate,
    }


def ingest_correction(db: Session, payload) -> dict:
    message = payload.validate_rules()
    if message:
        raise AppError(ErrorCode.VALIDATION, message, 422)

    member = db.scalar(select(Member).where(Member.member_no == payload.member_no))
    if member is None:
        raise AppError(ErrorCode.NOT_FOUND, f"会员 {payload.member_no} 不存在", 404)

    # 1) 更正幂等键：同一 correction_no 重复投递只生效一次
    if payload.correction_no:
        existing = db.scalar(
            select(OrderFact).where(OrderFact.correction_no == payload.correction_no)
        )
        if existing is not None:
            return {"item": _serialize(existing, duplicate=True), "duplicate": True}

    # 2) 事件自身 order_no 幂等（POS 至少不会换号重投）
    existing_order = db.scalar(select(OrderFact).where(OrderFact.order_no == payload.order_no))
    if existing_order is not None:
        return {"item": _serialize(existing_order, duplicate=True), "duplicate": True}

    order_type = payload.order_type
    original: OrderFact | None = None
    if order_type in (ORDER_TYPE_REFUND, ORDER_TYPE_VOID):
        original = db.scalar(
            select(OrderFact).where(OrderFact.order_no == payload.original_order_no)
        )
        if original is None:
            action = "撤销" if order_type == ORDER_TYPE_VOID else "退款"
            raise AppError(
                ErrorCode.NOT_FOUND,
                f"原销售单 {payload.original_order_no} 不存在，无法{action}",
                404,
            )
        if original.order_type not in SALE_TYPES:
            raise AppError(
                ErrorCode.VALIDATION,
                f"{payload.original_order_no} 不是销售单，不能作为更正对象",
                422,
            )
        if original.member_id != member.id:
            raise AppError(
                ErrorCode.VALIDATION,
                "更正单会员与原销售单会员不一致，拒绝跨会员冲减",
                422,
            )
        prior_void = db.scalar(
            select(OrderFact).where(
                OrderFact.original_order_no == original.order_no,
                OrderFact.order_type == ORDER_TYPE_VOID,
            )
        )
        if prior_void is not None:
            action = "撤销" if order_type == ORDER_TYPE_VOID else "退款"
            raise AppError(
                ErrorCode.VALIDATION,
                f"原销售单 {original.order_no} 已被撤销（{prior_void.correction_no}），不能再{action}",
                422,
            )
        if order_type == ORDER_TYPE_REFUND:
            # 同原单已生效退款的冲减金额合计（更正金额存负值，取负求和）
            refunded = (
                db.scalar(
                    select(
                        (-func.coalesce(func.sum(OrderFact.amount), 0)).label("refunded_amount")
                    ).where(
                        OrderFact.original_order_no == original.order_no,
                        OrderFact.order_type == ORDER_TYPE_REFUND,
                    )
                )
                or 0.0
            )
            if float(refunded) + payload.amount > float(original.amount) + 1e-6:
                raise AppError(
                    ErrorCode.VALIDATION,
                    (
                        f"退款金额超过原单可退余额：原单 ¥{float(original.amount):.2f}，"
                        f"已退 ¥{float(refunded):.2f}，本次 ¥{payload.amount:.2f}"
                    ),
                    422,
                )

    # 3) 落库：更正金额取负，销售金额取正
    signed_amount = -payload.amount if order_type in (ORDER_TYPE_REFUND, ORDER_TYPE_VOID) else payload.amount
    order = OrderFact(
        order_no=payload.order_no,
        correction_no=payload.correction_no,
        order_type=order_type,
        original_order_no=payload.original_order_no,
        member_id=member.id,
        store_code=payload.store_code,
        amount=round(float(signed_amount), 2),
        ordered_at=payload.ordered_at.replace(tzinfo=None),
        coupon_issued=payload.coupon_issued,
        coupon_redeemed=payload.coupon_redeemed,
        coupon_discount=payload.coupon_discount,
        received_at=datetime.now(timezone.utc),
    )
    db.add(order)
    try:
        db.commit()
    except IntegrityError:
        # 并发重复投递越过了上面的 SELECT 检查，由 order_no / correction_no 唯一索引兜底：
        # 回滚后回读已存在的那一条，仍按幂等语义返回，不二次冲减、不抛 500
        db.rollback()
        existing = db.scalar(
            select(OrderFact).where(
                (OrderFact.correction_no == payload.correction_no)
                if payload.correction_no
                else OrderFact.order_no == payload.order_no
            )
        )
        if existing is None:  # pragma: no cover - 唯一索引冲突必然能回读到冲突行
            existing = db.scalar(select(OrderFact).where(OrderFact.order_no == payload.order_no))
        return {"item": _serialize(existing, duplicate=True), "duplicate": True}
    db.refresh(order)
    return {"item": _serialize(order), "duplicate": False}
