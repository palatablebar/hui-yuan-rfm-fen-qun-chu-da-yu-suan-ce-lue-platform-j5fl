from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.config.database import get_db
from app.constants.roles import ROLE_ADMIN, ROLE_ANALYST
from app.controllers import order_controller
from app.middlewares.auth import get_current_user, require_roles
from app.models import User
from app.schemas.order_correction import CorrectionIngestRequest

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("/summary")
def summary(
    as_of: date | None = Query(default=None),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict:
    return order_controller.summary(db, as_of)


@router.post("/corrections")
def ingest_correction(
    payload: CorrectionIngestRequest,
    request: Request,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles(ROLE_ADMIN, ROLE_ANALYST)),
):
    """门店 POS 补传退款 / 撤销 / 迟到订单；correction_no 幂等，重复投递只生效一次。

    首次落库返回 201；同一 correction_no（或 order_no）重复投递返回 200 且 duplicate=true。
    """
    return order_controller.ingest_correction(db, request, payload)
