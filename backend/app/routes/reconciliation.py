from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.controllers import reconciliation_controller
from app.middlewares.auth import get_current_user
from app.models import User

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


@router.get("")
def reconciliation(
    current_run_id: int | None = Query(default=None),
    baseline_run_id: int | None = Query(default=None),
    total_budget: float | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict:
    """对账总览：原单、冲减金额、会员消费、分群人数与预算差异一次给齐。"""
    return reconciliation_controller.reconciliation(
        db, current_run_id, baseline_run_id, total_budget
    )


@router.get("/corrections")
def corrections(
    as_of: date | None = Query(default=None),
    kind: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict:
    """退款 / 撤销更正流水（含统计日之后未采纳的事件）。"""
    return reconciliation_controller.corrections(db, as_of, kind)
