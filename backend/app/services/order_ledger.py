"""POS 订单更正台账（纯 Python，不 import DB / web 框架）。

门店 POS 会补传退款单、撤销单与迟到订单，``order_facts`` 以事件溯源方式保存全部
事件（原单永不覆盖、永不删除）。本模块是「统计日 as-of 口径」的唯一实现，
rfm_engine、对账服务与 seed 共用，保证分群可复算、更正可追溯。

口径（写死，与 README §算法 1 同级版本化）：
1. 只认**业务发生时间** ``ordered_at <= as_of`` 的事件；统计日之后补传的销售单
   （迟到单）与统计日之后才发生的退款/撤销一律不进入该 as-of 的视图；
2. 撤销单（VOID）使原销售单整笔作废：原单不计订单数、不计 GMV、不参与 R/F/M；
3. 退款单（REFUND）只冲减金额，不冲减订单数（退款前确实发生过到店消费）；
   同一原单多笔退款逐笔累加，会员净消费最低为 0，不出现负 GMV；
4. 已撤销原单的退款不再重复冲减；原单本身在统计日之后的更正也无对象可冲减；
5. 同一 ``correction_no`` 的更正重复投递只生效一次（数据库唯一索引 + 本模块防御性去重）；
6. 退款单 / 撤销单本身永远不会进入会员名单口径（不按它们计数、不按它们建簇）。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable

from app.constants.order_types import (
    ALL_ORDER_TYPES,
    ORDER_TYPE_LATE_SALE,
    ORDER_TYPE_REFUND,
    ORDER_TYPE_SALE,
    ORDER_TYPE_VOID,
    SALE_TYPES,
)


def _to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)[:10]).date()


def _to_amount(value: Any) -> float:
    return round(float(value or 0.0), 2)


def build_order_ledger(events: Iterable[dict], as_of: date | str) -> dict:
    """把原始订单事件整理成 as-of 口径的更正台账。

    events 元素需含：order_no / member_id / amount / ordered_at / order_type /
    original_order_no / correction_no（后三者缺省按销售单处理，兼容历史数据）。

    返回：
    {
      "as_of": "YYYY-MM-DD",
      "sales":     原单视角行（含有效/已退款/已撤销/超期未采纳状态与冲减金额），
      "corrections": 更正事件视角行（含是否采纳、未采纳原因、引用原单），
      "effective_orders": 喂给 RFM 聚合的有效订单（已扣退款、已剔除撤销/超期），
      "summary":  分口径计数与 GMV 对账数，
    }
    """
    cutoff = _to_date(as_of)
    raw_events = sorted(events, key=lambda item: int(item.get("id") or 0))

    sales_by_no: dict[str, dict] = {}
    corrections: list[dict] = []
    seen_correction_nos: set[str] = set()

    for event in raw_events:
        order_type = event.get("order_type") or ORDER_TYPE_SALE
        if order_type not in ALL_ORDER_TYPES:
            order_type = ORDER_TYPE_SALE
        event_date = _to_date(event["ordered_at"])
        within = event_date <= cutoff

        if order_type in SALE_TYPES:
            sales_by_no[str(event["order_no"])] = {
                "order_no": str(event["order_no"]),
                "member_id": int(event["member_id"]),
                "store_code": event.get("store_code", ""),
                "order_type": order_type,
                "amount": _to_amount(event["amount"]),
                "ordered_at": event["ordered_at"],
                "event_date": event_date.isoformat(),
                "coupon_issued": bool(event.get("coupon_issued")),
                "coupon_redeemed": bool(event.get("coupon_redeemed")),
                "within_cutoff": within,
                "voided": False,
                "void_correction_no": None,
                "refunded_amount": 0.0,
                "refund_correction_nos": [],
            }
            continue

        # --- 更正事件（REFUND / VOID）---
        correction_no = event.get("correction_no") or str(event["order_no"])
        duplicate = correction_no in seen_correction_nos
        seen_correction_nos.add(correction_no)
        original_no = event.get("original_order_no")
        original = sales_by_no.get(str(original_no)) if original_no else None

        applied = False
        reason = ""
        if duplicate:
            reason = "duplicate_correction_no"
        elif not within:
            reason = "after_cutoff"
        elif original is None:
            reason = "original_order_missing"
        elif not original["within_cutoff"]:
            # 原销售单本身在统计日之后（as-of 视图里不存在），更正无对象可冲减
            reason = "original_after_cutoff"
        elif original["voided"]:
            reason = "original_already_voided"
        else:
            applied = True

        correction_row = {
            "order_no": str(event["order_no"]),
            "correction_no": correction_no,
            "order_type": order_type,
            "original_order_no": str(original_no) if original_no else None,
            "member_id": int(event["member_id"]),
            "store_code": event.get("store_code", ""),
            # DB 约定退款/撤销金额存负值；展示与冲减统一取绝对值
            "amount": _to_amount(event["amount"]),
            "reversal_amount": abs(_to_amount(event["amount"])),
            "ordered_at": event["ordered_at"],
            "event_date": event_date.isoformat(),
            "within_cutoff": within,
            "duplicate": duplicate,
            "applied": applied,
            "not_applied_reason": reason,
        }
        corrections.append(correction_row)

        if not applied:
            continue
        if order_type == ORDER_TYPE_VOID:
            original["voided"] = True
            original["void_correction_no"] = correction_no
        elif order_type == ORDER_TYPE_REFUND:
            original["refunded_amount"] = round(
                original["refunded_amount"] + correction_row["reversal_amount"], 2
            )
            original["refund_correction_nos"].append(correction_no)

    # --- 原单视角：净金额与状态 ---
    effective_orders: list[dict] = []
    sales_view: list[dict] = []
    counters = {
        "sale_orders": 0,
        "late_sale_orders": 0,
        "after_cutoff_sales": 0,
        "voided_orders": 0,
        "refunded_orders": 0,
        "effective_orders": 0,
        "gross_gmv": 0.0,
        "refund_amount": 0.0,
        "void_amount": 0.0,
        "net_gmv": 0.0,
    }
    for sale in sales_by_no.values():
        gross = sale["amount"]
        refunded = min(sale["refunded_amount"], gross)  # 单维度封顶，配合会员维度二次封顶
        net_amount = round(gross - refunded, 2)
        if sale["voided"]:
            status = "voided"
        elif not sale["within_cutoff"]:
            status = "after_cutoff"
        elif refunded > 0:
            status = "refunded" if net_amount > 0 else "fully_refunded"
        else:
            status = "effective"
        sale_view = {
            **sale,
            "refunded_amount": refunded,
            "net_amount": net_amount if status not in ("voided", "after_cutoff") else 0.0,
            "status": status,
        }
        sales_view.append(sale_view)

        if not sale["within_cutoff"]:
            counters["after_cutoff_sales"] += 1
            if sale["order_type"] == ORDER_TYPE_LATE_SALE:
                counters["late_sale_orders"] += 1
            continue
        if sale["order_type"] == ORDER_TYPE_LATE_SALE:
            counters["late_sale_orders"] += 1
        counters["sale_orders"] += 1
        counters["gross_gmv"] = round(counters["gross_gmv"] + gross, 2)
        if sale["voided"]:
            counters["voided_orders"] += 1
            counters["void_amount"] = round(counters["void_amount"] + gross, 2)
            continue
        if refunded > 0:
            counters["refunded_orders"] += 1
            counters["refund_amount"] = round(counters["refund_amount"] + refunded, 2)
        counters["effective_orders"] += 1
        counters["net_gmv"] = round(counters["net_gmv"] + net_amount, 2)
        effective_orders.append(
            {
                "order_no": sale["order_no"],
                "member_id": sale["member_id"],
                "store_code": sale["store_code"],
                "amount": net_amount,
                "gross_amount": gross,
                "refunded_amount": refunded,
                "ordered_at": sale["ordered_at"],
                "coupon_issued": sale["coupon_issued"],
                "coupon_redeemed": sale["coupon_redeemed"],
                "status": status,
            }
        )

    applied_corrections = [row for row in corrections if row["applied"]]
    summary = {
        **counters,
        "correction_total": len(corrections),
        "correction_applied": len(applied_corrections),
        "correction_refund_total": len([c for c in applied_corrections if c["order_type"] == ORDER_TYPE_REFUND]),
        "correction_void_total": len([c for c in applied_corrections if c["order_type"] == ORDER_TYPE_VOID]),
        "correction_duplicate_total": len([c for c in corrections if c["duplicate"]]),
        "correction_after_cutoff_total": len([c for c in corrections if not c["within_cutoff"]]),
    }
    return {
        "as_of": cutoff.isoformat(),
        "sales": sorted(sales_view, key=lambda row: row["order_no"]),
        "corrections": sorted(corrections, key=lambda row: (row["event_date"], row["order_no"])),
        "effective_orders": sorted(effective_orders, key=lambda row: row["order_no"]),
        "summary": summary,
    }


def member_consumption(ledger: dict) -> dict[int, dict]:
    """按会员汇总原口径（gross）与 as-of 净口径（net）消费，供对账页核对。

    净消费在会员维度二次封顶到 0，防止跨单退款把会员打成负 GMV。
    """
    result: dict[int, dict] = {}
    for sale in ledger["sales"]:
        if not sale["within_cutoff"]:
            continue
        mid = sale["member_id"]
        bucket = result.setdefault(
            mid,
            {
                "member_id": mid,
                "gross_amount": 0.0,
                "refund_amount": 0.0,
                "void_amount": 0.0,
                "net_amount": 0.0,
                "order_count": 0,
                "effective_order_count": 0,
            },
        )
        bucket["gross_amount"] = round(bucket["gross_amount"] + sale["amount"], 2)
        bucket["order_count"] += 1
        if sale["voided"]:
            bucket["void_amount"] = round(bucket["void_amount"] + sale["amount"], 2)
            continue
        bucket["refund_amount"] = round(bucket["refund_amount"] + sale["refunded_amount"], 2)
        bucket["effective_order_count"] += 1
    for bucket in result.values():
        net = bucket["gross_amount"] - bucket["refund_amount"] - bucket["void_amount"]
        bucket["net_amount"] = round(max(net, 0.0), 2)
    return result
