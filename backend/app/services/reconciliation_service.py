"""对账服务：把 POS 原单 / 更正台账与分群、预算结果逐笔核对。

五个必须能对上的维度（对账页消费）：
1. 原单与冲减金额：order_ledger 的销售视角 + 更正视角，含未采纳原因；
2. 会员消费：每个会员的原口径 gross、退款/撤销冲减、as-of 净消费 net；
3. 分群人数：两个批次间各簇 member_count / reachable_count 差异；
4. 预算差异：同预算重算（或关联策略）的每簇 budget / 券面额 / 预计核销差异；
5. 名单口径：退款单 / 撤销单不产生名单行，被撤销原单的会员按净金额重新分群。

全部只读，且不依赖批次 JSONB 快照——直接从订单事实重建台账，可独立复算。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants.error_codes import AppError, ErrorCode
from app.constants.order_types import ORDER_TYPE_REFUND, ORDER_TYPE_VOID
from app.models import Member, OrderFact, SegmentRun, Strategy
from app.services import budget_engine
from app.services.order_ledger import build_order_ledger, member_consumption


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


def _completed_run(db: Session, run_id: int) -> SegmentRun:
    run = db.get(SegmentRun, run_id)
    if run is None:
        raise AppError(ErrorCode.NOT_FOUND, "分群批次不存在", 404)
    if run.status != "completed" or not run.result:
        raise AppError(ErrorCode.RUN_NOT_READY, "分群批次尚未完成，无法对账", 409)
    return run


def _assignment_map(run: SegmentRun) -> dict[int, dict]:
    return {int(item["member_id"]): item for item in (run.result or {}).get("assignments") or []}


def _cluster_map(run: SegmentRun) -> dict[str, dict]:
    return {item["cluster_key"]: item for item in (run.result or {}).get("clusters") or []}


def _resolve_runs(db: Session, current_id: int | None, baseline_id: int | None) -> tuple[SegmentRun, SegmentRun | None]:
    if current_id is None:
        current = db.scalars(
            select(SegmentRun)
            .where(SegmentRun.status == "completed")
            .order_by(SegmentRun.id.desc())
            .limit(1)
        ).first()
        if current is None:
            raise AppError(ErrorCode.RUN_NOT_READY, "还没有已完成的分群批次，无法对账", 409)
    else:
        current = _completed_run(db, current_id)

    if baseline_id is not None:
        baseline = _completed_run(db, baseline_id)
        if baseline.id == current.id:
            raise AppError(ErrorCode.VALIDATION, "对账基线批次不能与当前批次相同", 422)
        return current, baseline

    earlier = db.scalars(
        select(SegmentRun)
        .where(SegmentRun.status == "completed", SegmentRun.id != current.id)
        .order_by(SegmentRun.id.desc())
        .limit(1)
    ).first()
    return current, earlier


def _order_reconciliation(ledger: dict) -> dict:
    sales = []
    for sale in ledger["sales"]:
        sales.append(
            {
                "order_no": sale["order_no"],
                "member_id": sale["member_id"],
                "store_code": sale["store_code"],
                "order_type": sale["order_type"],
                "ordered_at": sale["event_date"],
                "gross_amount": sale["amount"],
                "refunded_amount": sale["refunded_amount"],
                "net_amount": sale["net_amount"],
                "voided": sale["voided"],
                "void_correction_no": sale["void_correction_no"],
                "within_cutoff": sale["within_cutoff"],
                "status": sale["status"],
            }
        )
    corrections = []
    for row in ledger["corrections"]:
        corrections.append(
            {
                "order_no": row["order_no"],
                "correction_no": row["correction_no"],
                "order_type": row["order_type"],
                "original_order_no": row["original_order_no"],
                "member_id": row["member_id"],
                "reversal_amount": row["reversal_amount"],
                "ordered_at": row["event_date"],
                "within_cutoff": row["within_cutoff"],
                "applied": row["applied"],
                "duplicate": row["duplicate"],
                "not_applied_reason": row["not_applied_reason"],
            }
        )
    return {"sales": sales, "corrections": corrections, "summary": ledger["summary"]}


def _member_consumption_rows(
    db: Session,
    ledger: dict,
    current: SegmentRun,
    baseline: SegmentRun | None,
) -> list[dict]:
    consumption = member_consumption(ledger)
    members = db.scalars(select(Member).order_by(Member.id)).all()
    current_map = _assignment_map(current)
    baseline_map = _assignment_map(baseline) if baseline else {}
    rows = []
    for member in members:
        bucket = consumption.get(member.id)
        current_assign = current_map.get(member.id)
        baseline_assign = baseline_map.get(member.id) if baseline_map else None
        if bucket is None and current_assign is None:
            continue
        gross = bucket["gross_amount"] if bucket else 0.0
        refund = bucket["refund_amount"] if bucket else 0.0
        void = bucket["void_amount"] if bucket else 0.0
        net = bucket["net_amount"] if bucket else 0.0
        rows.append(
            {
                "member_id": member.id,
                "member_no": member.member_no,
                "name": member.name,
                "store_code": member.store_code,
                "store_name": member.store_name,
                "order_count": bucket["order_count"] if bucket else 0,
                "effective_order_count": bucket["effective_order_count"] if bucket else 0,
                "gross_amount": gross,
                "refund_amount": refund,
                "void_amount": void,
                "net_amount": net,
                "current_cluster": current_assign["cluster_key"] if current_assign else None,
                "baseline_cluster": baseline_assign["cluster_key"] if baseline_assign else None,
                "cluster_changed": (
                    baseline_assign is not None
                    and current_assign is not None
                    and baseline_assign["cluster_key"] != current_assign["cluster_key"]
                ),
            }
        )
    # 优先把有冲减或分群迁移的会员排前面
    rows.sort(key=lambda row: (-(row["refund_amount"] + row["void_amount"]), not row["cluster_changed"], row["member_no"]))
    return rows


def _cluster_differences(current: SegmentRun, baseline: SegmentRun | None) -> list[dict]:
    current_clusters = _cluster_map(current)
    baseline_clusters = _cluster_map(baseline) if baseline else {}
    keys = sorted(set(current_clusters) | set(baseline_clusters))
    rows = []
    for key in keys:
        cur = current_clusters.get(key)
        base = baseline_clusters.get(key)
        rows.append(
            {
                "cluster_key": key,
                "label": (cur or base).get("label"),
                "kind": (cur or base).get("kind"),
                "current_member_count": cur.get("member_count") if cur else 0,
                "baseline_member_count": base.get("member_count") if base else 0,
                "member_count_delta": (cur.get("member_count") if cur else 0)
                - (base.get("member_count") if base else 0),
                "current_reachable_count": cur.get("reachable_count") if cur else 0,
                "baseline_reachable_count": base.get("reachable_count") if base else 0,
                "reachable_delta": (cur.get("reachable_count") if cur else 0)
                - (base.get("reachable_count") if base else 0),
                "current_gmv": cur.get("gmv_total") if cur else 0.0,
                "baseline_gmv": base.get("gmv_total") if base else 0.0,
                "gmv_delta": round(
                    (cur.get("gmv_total") if cur else 0.0) - (base.get("gmv_total") if base else 0.0),
                    2,
                ),
                "current_redemption_rate": cur.get("redemption_rate") if cur else 0.0,
                "baseline_redemption_rate": base.get("redemption_rate") if base else 0.0,
                "present_in_both": bool(cur and base),
            }
        )
    return rows


def _budget_for_run(run: SegmentRun, total_budget: float | None) -> dict | None:
    """用批次簇画像在指定预算下重算分配（快照复算，不写库）。"""
    clusters = (run.result or {}).get("clusters") or []
    if not clusters:
        return None
    budget = total_budget if total_budget is not None else 8000
    try:
        return budget_engine.allocate_budget(clusters, budget)
    except budget_engine.BudgetError:
        return None


def _budget_differences(
    db: Session,
    current: SegmentRun,
    baseline: SegmentRun | None,
    total_budget: float | None,
) -> dict:
    if total_budget is None:
        strategy = db.scalars(
            select(Strategy).where(Strategy.run_id == current.id).order_by(Strategy.id.desc()).limit(1)
        ).first()
        if strategy is not None:
            total_budget = float(strategy.total_budget)
        else:
            total_budget = 8000.0

    current_alloc = _budget_for_run(current, total_budget)
    baseline_alloc = _budget_for_run(baseline, total_budget) if baseline else None
    cur_map = {item["cluster_key"]: item for item in (current_alloc or {}).get("allocations", [])}
    base_map = {item["cluster_key"]: item for item in (baseline_alloc or {}).get("allocations", [])}
    rows = []
    for key in sorted(set(cur_map) | set(base_map)):
        cur = cur_map.get(key)
        base = base_map.get(key)
        rows.append(
            {
                "cluster_key": key,
                "total_budget": total_budget,
                "current_budget": cur.get("budget") if cur else 0,
                "baseline_budget": base.get("budget") if base else 0,
                "budget_delta": (cur.get("budget") if cur else 0) - (base.get("budget") if base else 0),
                "current_coupon_face": cur.get("coupon_face") if cur else None,
                "baseline_coupon_face": base.get("coupon_face") if base else None,
                "current_expected_redemptions": cur.get("expected_redemptions") if cur else 0.0,
                "baseline_expected_redemptions": base.get("expected_redemptions") if base else 0.0,
                "current_expected_gmv": cur.get("expected_gmv") if cur else 0.0,
                "baseline_expected_gmv": base.get("expected_gmv") if base else 0.0,
                "expected_gmv_delta": round(
                    (cur.get("expected_gmv") if cur else 0.0)
                    - (base.get("expected_gmv") if base else 0.0),
                    2,
                ),
                "current_roi": cur.get("roi") if cur else 0.0,
                "baseline_roi": base.get("roi") if base else 0.0,
            }
        )
    return {
        "total_budget": total_budget,
        "current_totals": (current_alloc or {}).get("totals"),
        "baseline_totals": (baseline_alloc or {}).get("totals"),
        "allocations": rows,
    }


def build_reconciliation(
    db: Session,
    current_run_id: int | None = None,
    baseline_run_id: int | None = None,
    total_budget: float | None = None,
) -> dict:
    current, baseline = _resolve_runs(db, current_run_id, baseline_run_id)
    cutoff = current.as_of
    ledger = build_order_ledger(_load_events(db), cutoff)

    # 交叉校验：台账净 GMV 必须与批次快照里的净 GMV 一致（同 as-of 复算）
    snapshot_net = (current.result or {}).get("gmv_total")
    ledger_net = ledger["summary"]["net_gmv"]
    net_matches = snapshot_net is None or abs(float(snapshot_net) - float(ledger_net)) < 0.01

    return {
        "as_of": cutoff.isoformat(),
        "current_run": {
            "id": current.id,
            "run_no": current.run_no,
            "as_of": current.as_of.isoformat(),
            "selected_k": current.selected_k,
            "member_count": current.member_count,
            "gmv_total": (current.result or {}).get("gmv_total"),
            "gross_gmv_total": (current.result or {}).get("gross_gmv_total"),
            "refund_gmv_total": (current.result or {}).get("refund_gmv_total"),
        },
        "baseline_run": (
            {
                "id": baseline.id,
                "run_no": baseline.run_no,
                "as_of": baseline.as_of.isoformat(),
                "selected_k": baseline.selected_k,
                "member_count": baseline.member_count,
                "gmv_total": (baseline.result or {}).get("gmv_total"),
            }
            if baseline
            else None
        ),
        "consistency": {
            "snapshot_net_gmv": snapshot_net,
            "ledger_net_gmv": ledger_net,
            "net_gmv_matches": net_matches,
        },
        "order_reconciliation": _order_reconciliation(ledger),
        "member_consumption": _member_consumption_rows(db, ledger, current, baseline),
        "cluster_differences": _cluster_differences(current, baseline),
        "budget_differences": _budget_differences(db, current, baseline, total_budget),
    }


def list_corrections(db: Session, as_of: date | None = None, kind: str | None = None) -> dict:
    """更正事件流水（对账页「冲减明细」表），默认全量，可按类型与统计日过滤。"""
    stmt = select(OrderFact).where(OrderFact.order_type.in_([ORDER_TYPE_REFUND, ORDER_TYPE_VOID]))
    if kind in (ORDER_TYPE_REFUND, ORDER_TYPE_VOID):
        stmt = stmt.where(OrderFact.order_type == kind)
    rows = db.scalars(stmt.order_by(OrderFact.ordered_at.desc(), OrderFact.id.desc())).all()
    member_map = {m.id: m for m in db.scalars(select(Member)).all()}
    items = []
    for order in rows:
        if as_of is not None and order.ordered_at.date() > as_of:
            continue
        member = member_map.get(order.member_id)
        items.append(
            {
                "order_no": order.order_no,
                "correction_no": order.correction_no,
                "order_type": order.order_type,
                "original_order_no": order.original_order_no,
                "member_no": member.member_no if member else None,
                "member_name": member.name if member else None,
                "store_code": order.store_code,
                "reversal_amount": abs(float(order.amount)),
                "ordered_at": order.ordered_at.isoformat(),
                "received_at": order.received_at.isoformat() if order.received_at else None,
            }
        )
    return {"items": items, "total": len(items)}
