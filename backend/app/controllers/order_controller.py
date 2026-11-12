"""订单事实与 POS 补传更正接口。"""
from datetime import date

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.middlewares.audit_logger import record
from app.schemas.order_correction import CorrectionIngestRequest
from app.services import order_correction_service, order_service


def summary(db: Session, as_of: date | None = None) -> dict:
    return order_service.order_summary(db, as_of)


def ingest_correction(db: Session, request: Request, payload: CorrectionIngestRequest):
    result = order_correction_service.ingest_correction(db, payload)
    item = result["item"]
    record(
        request,
        "order.correction.ingest",
        "order_fact",
        entity_id=None,
        after={
            "order_no": item["order_no"],
            "correction_no": item["correction_no"],
            "order_type": item["order_type"],
            "original_order_no": item["original_order_no"],
            "amount": item["amount"],
            "duplicate": result["duplicate"],
        },
    )
    # 首次落库 201；同一更正重复投递不产生新资源，返回 200 + duplicate=true
    return JSONResponse(status_code=200 if result["duplicate"] else 201, content=result)
