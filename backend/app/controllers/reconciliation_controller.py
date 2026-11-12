"""对账接口：原单/冲减/会员消费/分群人数/预算差异只读核对。"""
from datetime import date

from sqlalchemy.orm import Session

from app.constants.order_types import ORDER_TYPE_REFUND, ORDER_TYPE_VOID
from app.services import reconciliation_service


def reconciliation(
    db: Session,
    current_run_id: int | None,
    baseline_run_id: int | None,
    total_budget: float | None,
) -> dict:
    return reconciliation_service.build_reconciliation(
        db, current_run_id, baseline_run_id, total_budget
    )


def corrections(db: Session, as_of: date | None, kind: str | None) -> dict:
    if kind not in (None, ORDER_TYPE_REFUND, ORDER_TYPE_VOID):
        kind = None
    return reconciliation_service.list_corrections(db, as_of, kind)
