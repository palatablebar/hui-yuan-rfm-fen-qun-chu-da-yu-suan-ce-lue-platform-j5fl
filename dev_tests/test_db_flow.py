"""DB 集成验证（SQLite 内存库）：幂等落库 → 分群 → 对账，全链路。

运行：python3 -I /workspace/dev_tests/test_db_flow.py
"""
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, "/home/node/.local/lib/python3.11/site-packages")
sys.path.insert(0, "/workspace/backend")

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app.config.database import Base
import app.models  # noqa: F401  注册模型


@compiles(JSONB, "sqlite")
def _sqlite_jsonb(element, compiler, **kw):  # 测试环境把 PG JSONB 渲染成 SQLite JSON
    return compiler.visit_json(element, **kw) if hasattr(compiler, "visit_json") else "JSON"
from app.constants.order_types import ORDER_TYPE_REFUND, ORDER_TYPE_VOID
from app.models import Member, OrderFact, SegmentRun
from app.schemas.order_correction import CorrectionIngestRequest
from app.services import order_correction_service, order_service, reconciliation_service, segment_service
from app.services import rfm_engine

engine = create_engine("sqlite://", future=True)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine, future=True)
db = Session()


def check(name, got, want):
    assert got == want, f"{name}: got {got!r}, want {want!r}"
    print(f"  ok  {name} = {got!r}")


# --- 造基础数据：120 会员 + 每人 3 单 ---
for i in range(1, 121):
    db.add(Member(member_no=f"SG-M{i:04d}", name=f"n{i}", phone="138",
                  store_code="SH01", store_name="梧桐里店", joined_at=date(2024, 1, 1), consent=True))
db.commit()
for i in range(1, 121):
    for d in (1, 4, 8):
        db.add(OrderFact(order_no=f"SG-O{i:04d}-{d}", member_id=i, store_code="SH01",
                         amount=100.0 + i, ordered_at=datetime(2026, 11, d, 10),
                         order_type="SALE"))
db.commit()

print("== 1. 更正落库与幂等 ==")
target = db.scalar(select(OrderFact).where(OrderFact.order_no == "SG-O0001-1"))
refund_payload = CorrectionIngestRequest(
    order_no="SG-R0001", correction_no="CR-0001", order_type="REFUND",
    original_order_no="SG-O0001-1", member_no="SG-M0001", store_code="SH01",
    amount=40.0, ordered_at=datetime(2026, 11, 14, 10),
)
r1 = order_correction_service.ingest_correction(db, refund_payload)
check("首次退款落库", r1["duplicate"], False)
check("退款金额转负", r1["item"]["amount"], -40.0)
# 重复投递：同一 correction_no，换新 order_no
dup_payload = refund_payload.model_copy(update={"order_no": "SG-R0001-DUP"})
r2 = order_correction_service.ingest_correction(db, dup_payload)
check("重复投递幂等返回", r2["duplicate"], True)
check("重复投递返回首条 order_no", r2["item"]["order_no"], "SG-R0001")
check("库里退款事件仍只有 1 条",
      db.scalar(select(func.count()).select_from(OrderFact).where(OrderFact.order_type == "REFUND")), 1)

# 业务校验：超额退款 422
over = refund_payload.model_copy(update={"order_no": "SG-R0002", "correction_no": "CR-0002", "amount": 9999.0})
try:
    order_correction_service.ingest_correction(db, over)
    raise AssertionError("超额退款应当被拒绝")
except Exception as exc:
    check("超额退款被拒绝", getattr(exc, "status_code", None), 422)

# 撤销 + 撤销后再退款
void_payload = CorrectionIngestRequest(
    order_no="SG-V0001", correction_no="CR-V001", order_type="VOID",
    original_order_no="SG-O0002-1", member_no="SG-M0002", store_code="SH01",
    amount=102.0, ordered_at=datetime(2026, 11, 15, 9),
)
order_correction_service.ingest_correction(db, void_payload)
refund_after_void = CorrectionIngestRequest(
    order_no="SG-R0003", correction_no="CR-0003", order_type="REFUND",
    original_order_no="SG-O0002-1", member_no="SG-M0002", store_code="SH01",
    amount=10.0, ordered_at=datetime(2026, 11, 16, 9),
)
try:
    order_correction_service.ingest_correction(db, refund_after_void)
    raise AssertionError("撤销后退款应当被拒绝")
except Exception as exc:
    check("撤销后退款被拒绝", getattr(exc, "status_code", None), 422)

# 迟到销售单
late_payload = CorrectionIngestRequest(
    order_no="SG-L0001", order_type="LATE_SALE", member_no="SG-M0003", store_code="SH01",
    amount=88.0, ordered_at=datetime(2026, 11, 20, 9),
)
order_correction_service.ingest_correction(db, late_payload)
check("迟到单金额为正", db.scalar(select(OrderFact.amount).where(OrderFact.order_no == "SG-L0001")), 88.0)

print("== 2. 基线分群（as-of 2026-11-12，更正全在统计日后）==")
members = db.scalars(select(Member)).all()
orders = db.scalars(select(OrderFact)).all()
member_rows = [{"member_id": m.id, "member_no": m.member_no, "name": m.name,
                "store_code": m.store_code, "store_name": m.store_name,
                "consent": m.consent, "joined_at": m.joined_at} for m in members]
order_rows = [{"id": o.id, "order_no": o.order_no, "member_id": o.member_id,
               "amount": float(o.amount), "ordered_at": o.ordered_at,
               "coupon_issued": o.coupon_issued, "coupon_redeemed": o.coupon_redeemed,
               "order_type": o.order_type, "original_order_no": o.original_order_no,
               "correction_no": o.correction_no, "store_code": o.store_code} for o in orders]
base_result = rfm_engine.run_segmentation(member_rows, order_rows, as_of=date(2026, 11, 12), k=3)
check("基线净 GMV 不受统计日后更正影响",
      base_result["gmv_total"], round(sum(100.0 + i for i in range(1, 121)) * 3, 2))
check("基线有效订单=360", base_result["order_ledger"]["effective_orders"], 360)

# 落两个批次，验对账
run1 = SegmentRun(run_no="SEG-20261112-001", name="基线", status="completed", k_requested="3",
                  as_of=date(2026, 11, 12), random_state=5741, selected_k=3,
                  silhouette=0.4, inertia=1.0, member_count=120, result=base_result)
db.add(run1)
db.commit()

print("== 3. 更正批次（as-of 2026-11-30，采纳全部更正）==")
new_result = rfm_engine.run_segmentation(member_rows, order_rows, as_of=date(2026, 11, 30), k=3)
ledger = new_result["order_ledger"]
check("更正批次采纳 1 退款 1 撤销", ledger["correction_applied"], 2)
check("更正批次退款冲减", ledger["refund_amount"], 40.0)
check("更正批次撤销金额", ledger["void_amount"], 102.0)
check("更正批次迟到单计入销售", ledger["late_sale_orders"], 1)
check("更正批次有效订单=360-1撤销+1迟到", ledger["effective_orders"], 360)
assign = {a["member_id"]: a for a in new_result["assignments"]}
check("M1 净消费=原303-退40", assign[1]["monetary"], 263.0)
check("M1 F 仍为 3（退款不冲单数）", assign[1]["frequency"], 3)
check("M2 撤销后 F=2", assign[2]["frequency"], 2)
check("M3 迟到单计入 F=4", assign[3]["frequency"], 4)

run2 = SegmentRun(run_no="SEG-20261130-001", name="更正后", status="completed", k_requested="3",
                  as_of=date(2026, 11, 30), random_state=5741, selected_k=3,
                  silhouette=0.4, inertia=1.0, member_count=120, result=new_result)
db.add(run2)
db.commit()

print("== 4. 对账服务 ==")
recon = reconciliation_service.build_reconciliation(db, current_run_id=run2.id, baseline_run_id=run1.id)
check("快照净 GMV 与台账复算一致", recon["consistency"]["net_gmv_matches"], True)
check("对账含原单视角", len(recon["order_reconciliation"]["sales"]) > 0, True)
check("对账含更正视角（重复投递已在写入侧幂等拦截，库里仅 2 条事件）",
      len(recon["order_reconciliation"]["corrections"]), 2)
check("更正流水 applied 数",
      sum(1 for c in recon["order_reconciliation"]["corrections"] if c["applied"]), 2)
check("库内无重复更正事件",
      db.scalar(select(func.count()).select_from(OrderFact).where(OrderFact.order_type.in_(["REFUND", "VOID"]))), 2)
check("含会员消费核对行", len(recon["member_consumption"]), 120)
check("含分群人数差异", len(recon["cluster_differences"]), 3)
check("含预算差异", len(recon["budget_differences"]["allocations"]), 3)
m1_row = next(r for r in recon["member_consumption"] if r["member_no"] == "SG-M0001")
check("对账 M1 原口径", m1_row["gross_amount"], 303.0)
check("对账 M1 退款", m1_row["refund_amount"], 40.0)
check("对账 M1 净口径", m1_row["net_amount"], 263.0)

print("== 5. 订单摘要 as-of 口径 ==")
summary = order_service.order_summary(db, date(2026, 11, 30))
check("摘要有效订单", summary["totals"]["orders"], 360)
check("摘要净 GMV 与台账一致", summary["totals"]["gmv"], new_result["gmv_total"])
check("摘要更正概览退款数", summary["correction_overview"]["refund_amount"], 40.0)
summary_base = order_service.order_summary(db, date(2026, 11, 12))
check("基线摘要仍是 360 单", summary_base["totals"]["orders"], 360)
check("基线摘要看不到退款", summary_base["correction_overview"]["refund_amount"], 0.0)

print("== 6. 名单不含退款/撤销单本身 ==")
# 所有 assignment 的 member_id 都是会员，没有任何 order_no 层面的更正行；
# 被撤销整单的 M2 仍在名单（按净口径重新分群），其 F/M 已更新
m2_row = next(r for r in recon["member_consumption"] if r["member_no"] == "SG-M0002")
check("M2 仍在会员名单（按净口径）", m2_row["member_no"], "SG-M0002")
check("M2 撤销金额可见", m2_row["void_amount"], 102.0)
check("M2 净消费剔除撤销", m2_row["net_amount"], 204.0)

print("ALL DB-FLOW TESTS PASSED", file=sys.stderr)
