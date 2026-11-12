"""订单事实聚合：门店订单量 / GMV / 券核销率 + as-of 更正口径。

OrderFact 是 RFM 分群的唯一事实来源；本服务只读聚合，供 RFM 明细页展示「订单事实」
侧的门店基数与券历史。POS 补传退款/撤销/迟到单后，本服务同时给出：
- ``raw``：事件表原始口径（含退款/撤销/迟到事件，仅说明系统收到了什么）；
- ``effective``：统计日 as-of 的有效口径（经 order_ledger 冲减后，分群实际使用的数）。
"""
from datetime import date

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import Session

from app.models import Member, OrderFact
from app.services.order_ledger import build_order_ledger
from app.utils.formatting import ratio


def _load_events(db: Session) -> list[dict]:
    orders = db.scalars(select(OrderFact).order_by(OrderFact.id)).all()
    return [
        {
            "id": order.id,
            "order_no": order.order_no,
            "member_id": order.member_id,
            "store_code": order.store_code,
            "amount": float(order.amount),
            "ordered_at": order.ordered_at,
            "coupon_issued": order.coupon_issued,
            "coupon_redeemed": order.coupon_redeemed,
            "order_type": order.order_type,
            "original_order_no": order.original_order_no,
            "correction_no": order.correction_no,
        }
        for order in orders
    ]


def _raw_store_rows(db: Session) -> list[dict]:
    rows = db.execute(
        select(
            OrderFact.store_code,
            Member.store_name,
            func.count(OrderFact.id),
            func.coalesce(func.sum(OrderFact.amount), 0),
            func.coalesce(func.sum(cast(OrderFact.coupon_issued, Integer)), 0),
            func.coalesce(func.sum(cast(OrderFact.coupon_redeemed, Integer)), 0),
        )
        .join(Member, Member.id == OrderFact.member_id)
        .group_by(OrderFact.store_code, Member.store_name)
        .order_by(OrderFact.store_code)
    ).all()
    return [
        {
            "store_code": store_code,
            "store_name": store_name,
            "events": int(events),
            "amount_sum": float(amount or 0),
            "coupon_issued": int(issued or 0),
            "coupon_redeemed": int(redeemed or 0),
        }
        for store_code, store_name, events, amount, issued, redeemed in rows
    ]


def order_summary(db: Session, as_of: date | None = None) -> dict:
    from app.constants.rfm import REFERENCE_DATE

    cutoff = as_of or REFERENCE_DATE
    events = _load_events(db)
    ledger = build_order_ledger(events, cutoff)
    raw_rows = {row["store_code"]: row for row in _raw_store_rows(db)}

    # 有效口径按门店聚合（只统计 as-of 内、未撤销、已扣退款的有效订单）
    effective_by_store: dict[str, dict] = {}
    for order in ledger["effective_orders"]:
        bucket = effective_by_store.setdefault(
            order["store_code"],
            {"orders": 0, "gmv": 0.0, "coupon_issued": 0, "coupon_redeemed": 0},
        )
        bucket["orders"] += 1
        bucket["gmv"] = round(bucket["gmv"] + float(order["amount"]), 2)
        if order.get("coupon_issued"):
            bucket["coupon_issued"] += 1
            if order.get("coupon_redeemed"):
                bucket["coupon_redeemed"] += 1

    stores = []
    totals = {"orders": 0, "gmv": 0.0, "coupon_issued": 0, "coupon_redeemed": 0}
    store_names = {member.store_code: member.store_name for member in db.scalars(select(Member)).all()}
    for store_code in sorted(set(raw_rows) | set(effective_by_store)):
        effective = effective_by_store.get(
            store_code, {"orders": 0, "gmv": 0.0, "coupon_issued": 0, "coupon_redeemed": 0}
        )
        raw = raw_rows.get(store_code, {})
        row = {
            "store_code": store_code,
            "store_name": store_names.get(store_code, raw.get("store_name", "")),
            "orders": effective["orders"],
            "gmv": round(effective["gmv"], 2),
            "coupon_issued": effective["coupon_issued"],
            "coupon_redeemed": effective["coupon_redeemed"],
            "redemption_rate": ratio(effective["coupon_redeemed"], effective["coupon_issued"]),
            "raw_events": int(raw.get("events", 0)),
            "raw_amount_sum": round(float(raw.get("amount_sum", 0.0)), 2),
        }
        stores.append(row)
        totals["orders"] += row["orders"]
        totals["gmv"] = round(totals["gmv"] + row["gmv"], 2)
        totals["coupon_issued"] += row["coupon_issued"]
        totals["coupon_redeemed"] += row["coupon_redeemed"]

    totals["redemption_rate"] = ratio(totals["coupon_redeemed"], totals["coupon_issued"])
    summary = ledger["summary"]
    return {
        "as_of": cutoff.isoformat(),
        "totals": totals,
        "stores": stores,
        "correction_overview": {
            "sale_orders": summary["sale_orders"],
            "effective_orders": summary["effective_orders"],
            "late_sale_orders": summary["late_sale_orders"],
            "after_cutoff_sales": summary["after_cutoff_sales"],
            "voided_orders": summary["voided_orders"],
            "refunded_orders": summary["refunded_orders"],
            "gross_gmv": summary["gross_gmv"],
            "refund_amount": summary["refund_amount"],
            "void_amount": summary["void_amount"],
            "net_gmv": summary["net_gmv"],
            "correction_total": summary["correction_total"],
            "correction_applied": summary["correction_applied"],
            "correction_duplicate_total": summary["correction_duplicate_total"],
        },
    }
