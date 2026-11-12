"""幂等演示 seed：拾光咖啡 8 门店 / 600 会员 / 2400 订单 / POS 更正事件 / 2 次分群 / 3 账号。

幂等策略：
- 用户按 email、会员按 member_no、订单按 order_no 做自然键判重（已存在则不改）；
- 更正事件按 order_no 判重、按 correction_no 保证同一笔更正只生效一次；
- 演示分群批次按固定 run_no 判重，已存在则不重算；
- 结束时打印一行固定格式 SEED_SUMMARY，连续两次运行逐字一致。

两个演示批次：
- SEG-20261112-001（as-of 2026-11-12）：基线，全部更正事件都在统计日之后，结果与历史口径逐字一致；
- SEG-20261130-001（as-of 2026-11-30）：采纳退款/撤销/迟到单后的更正批次，用于对账页核对差异。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from app.config.database import SessionLocal
from app.constants.rfm import REFERENCE_DATE, RANDOM_STATE
from app.constants.roles import ROLE_ADMIN, ROLE_ANALYST, ROLE_VIEWER
from app.models import Member, OrderFact, SegmentRun, User
from app.services import segment_service
from app.utils.logger import log_event
from app.utils.password import hash_password
from scripts.seed_data import (
    CORRECTION_RUN_DATE,
    STORES,
    build_correction_events,
    build_demo_dataset,
)

DEMO_PASSWORD = "Demo@2026"
SEED_RUN_NO = "SEG-20261112-001"
CORRECTION_RUN_NO = "SEG-20261130-001"

DEMO_USERS = [
    {
        "email": "admin@rfmseg.io",
        "display_name": "沈知远",
        "role": ROLE_ADMIN,
    },
    {
        "email": "analyst@rfmseg.io",
        "display_name": "林晚照",
        "role": ROLE_ANALYST,
    },
    {
        "email": "viewer@rfmseg.io",
        "display_name": "周启明",
        "role": ROLE_VIEWER,
    },
]


def _ensure_users(db) -> dict[str, User]:
    users: dict[str, User] = {}
    for spec in DEMO_USERS:
        user = db.scalar(select(User).where(User.email == spec["email"]))
        if user is None:
            user = User(
                email=spec["email"],
                display_name=spec["display_name"],
                role=spec["role"],
                password_hash=hash_password(DEMO_PASSWORD),
                is_active=True,
            )
            db.add(user)
            db.flush()
        users[spec["role"]] = user
    db.commit()
    return users


def _ensure_members(db, member_rows: list[dict]) -> None:
    existing = set(db.scalars(select(Member.member_no)).all())
    pending = []
    for row in member_rows:
        if row["member_no"] in existing:
            continue
        pending.append(
            Member(
                member_no=row["member_no"],
                name=row["name"],
                phone=row["phone"],
                store_code=row["store_code"],
                store_name=row["store_name"],
                joined_at=row["joined_at"],
                consent=row["consent"],
            )
        )
    if pending:
        db.add_all(pending)
    db.commit()


def _ensure_orders(db, order_rows: list[dict]) -> None:
    member_map = {
        member_no: member_id
        for member_no, member_id in db.execute(select(Member.member_no, Member.id)).all()
    }
    existing = set(db.scalars(select(OrderFact.order_no)).all())
    seen_correction_nos = set(
        no for no in db.scalars(select(OrderFact.correction_no)).all() if no
    )
    pending = []
    for row in order_rows:
        if row["order_no"] in existing:
            continue
        correction_no = row.get("correction_no")
        if correction_no:
            if correction_no in seen_correction_nos:
                # 同一笔更正重复投递（跨批或同批）：跳过，不产生新行、不二次冲减
                continue
            seen_correction_nos.add(correction_no)
        is_correction = row.get("order_type") in ("REFUND", "VOID")
        signed_amount = -row["amount"] if is_correction else row["amount"]
        pending.append(
            OrderFact(
                order_no=row["order_no"],
                correction_no=correction_no,
                order_type=row.get("order_type", "SALE"),
                original_order_no=row.get("original_order_no"),
                member_id=member_map[row["member_no"]],
                store_code=row["store_code"],
                amount=signed_amount,
                ordered_at=row["ordered_at"],
                coupon_issued=row["coupon_issued"],
                coupon_redeemed=row["coupon_redeemed"],
                coupon_discount=row["coupon_discount"],
                received_at=datetime.now(timezone.utc),
            )
        )
    if pending:
        db.add_all(pending)
    db.commit()


def _ensure_segment(db, admin: User, run_no: str, name: str, as_of) -> None:
    run = db.scalar(select(SegmentRun).where(SegmentRun.run_no == run_no))
    if run is not None:
        return
    run = SegmentRun(
        run_no=run_no,
        name=name,
        status="queued",
        k_requested="auto",
        as_of=as_of,
        random_state=RANDOM_STATE,
        created_by=admin.id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    segment_service.execute_run_sync(db, run)


def run_seed() -> dict:
    db = SessionLocal()
    try:
        users = _ensure_users(db)
        dataset = build_demo_dataset()
        _ensure_members(db, dataset["members"])
        _ensure_orders(db, dataset["orders"])

        corrections = build_correction_events(dataset)
        _ensure_orders(db, corrections)

        _ensure_segment(db, users[ROLE_ADMIN], SEED_RUN_NO, "2026-11 会员 RFM 基线分群", REFERENCE_DATE)
        _ensure_segment(
            db,
            users[ROLE_ADMIN],
            CORRECTION_RUN_NO,
            "2026-11-30 POS 退款/撤销/迟到单更正后分群",
            CORRECTION_RUN_DATE,
        )

        member_count = db.scalar(select(func.count()).select_from(Member)) or 0
        order_count = db.scalar(select(func.count()).select_from(OrderFact)) or 0
        segment_count = db.scalar(select(func.count()).select_from(SegmentRun)) or 0
        user_count = db.scalar(select(func.count()).select_from(User)) or 0
    finally:
        db.close()

    summary = {
        "brand": "rfmseg",
        "stores": len(STORES),
        "members": int(member_count),
        "orders": int(order_count),
        "segments": int(segment_count),
        "users": int(user_count),
    }
    print(
        "SEED_SUMMARY brand={brand} stores={stores} members={members} "
        "orders={orders} segments={segments} users={users}".format(**summary),
        flush=True,
    )
    return summary


if __name__ == "__main__":
    log_event("SEED_SUMMARY_RUN", **run_seed())
