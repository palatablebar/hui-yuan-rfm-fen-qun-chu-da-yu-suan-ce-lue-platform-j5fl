"""HTTP 端到端验证（FastAPI TestClient + SQLite）：登录 / RBAC / 幂等更正 / 分群 / 对账。

运行：python3 -I /workspace/dev_tests/test_api_e2e.py
"""
import os
import sys
import tempfile
from datetime import date, datetime

sys.path.insert(0, "/home/node/.local/lib/python3.11/site-packages")
sys.path.insert(0, "/workspace/backend")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  先注册模型
from app.config.database import Base, get_db
from app.config import database as db_cfg
from app.middlewares import audit_logger
from app.main import app
from app.models import Member, OrderFact, User
from app.utils.password import hash_password


@compiles(JSONB, "sqlite")
def _j(element, compiler, **kw):
    return "JSON"


_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".sqlite")
os.close(_DB_FD)
engine = create_engine(
    f"sqlite:///{_DB_PATH}",
    future=True,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base.metadata.create_all(engine)
# 路由与审计中间件都改走测试会话
db_cfg.engine = engine
db_cfg.SessionLocal = TestingSession
audit_logger.SessionLocal = TestingSession


def override_get_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)  # 不进入上下文，跳过 lifespan（不连 PG、不跑 seed）


def check(name, got, want):
    assert got == want, f"{name}: got {got!r}, want {want!r}"
    print(f"  ok  {name} = {got!r}")


# --- 基础数据 ---
with TestingSession() as db:
    db.add_all(
        [
            User(email="admin@rfmseg.io", display_name="主管", role="admin",
                 password_hash=hash_password("Demo@2026"), is_active=True),
            User(email="viewer@rfmseg.io", display_name="店长", role="viewer",
                 password_hash=hash_password("Demo@2026"), is_active=True),
        ]
    )
    for i in range(1, 121):
        db.add(Member(member_no=f"SG-M{i:04d}", name=f"n{i}", phone="138", store_code="SH01",
                      store_name="梧桐里店", joined_at=date(2024, 1, 1), consent=True))
    db.commit()
    for i in range(1, 121):
        for d in (1, 4, 8):
            db.add(OrderFact(order_no=f"SG-O{i:04d}-{d}", member_id=i, store_code="SH01",
                             amount=100.0 + i, ordered_at=datetime(2026, 11, d, 10), order_type="SALE"))
    db.commit()

print("== 1. 登录与鉴权 ==")
r = client.post("/auth/login", json={"email": "admin@rfmseg.io", "password": "Demo@2026"})
check("管理员登录", r.status_code, 200)
token = r.json()["access_token"]
H = {"Authorization": f"Bearer {token}"}
r = client.get("/orders/summary")
check("无 Token 401", r.status_code, 401)

r = client.post("/auth/login", json={"email": "viewer@rfmseg.io", "password": "Demo@2026"})
viewer_token = r.json()["access_token"]
HV = {"Authorization": f"Bearer {viewer_token}"}

print("== 2. POS 更正投递（RBAC + 幂等）==")
refund = {
    "order_no": "SG-R0001", "correction_no": "CR-0001", "order_type": "REFUND",
    "original_order_no": "SG-O0001-1", "member_no": "SG-M0001", "store_code": "SH01",
    "amount": 40.0, "ordered_at": "2026-11-14T10:00:00",
}
r = client.post("/orders/corrections", json=refund, headers=HV)
check("viewer 投递更正被拒 403", r.status_code, 403)
r = client.post("/orders/corrections", json=refund, headers=H)
check("admin 退款投递 201", r.status_code, 201)
check("首次非重复", r.json()["duplicate"], False)
r = client.post("/orders/corrections", json={**refund, "order_no": "SG-R0001-DUP"}, headers=H)
check("重复投递 200 语义返回", r.status_code, 200)
check("重复标记", r.json()["duplicate"], True)
check("重复返回首条单号", r.json()["item"]["order_no"], "SG-R0001")

# 非法更正：缺原单号
r = client.post("/orders/corrections",
                json={**refund, "order_no": "SG-R0002", "correction_no": "CR-0002",
                      "original_order_no": None}, headers=H)
check("退款缺原单 422", r.status_code, 422)
# 原单不存在
r = client.post("/orders/corrections",
                json={**refund, "order_no": "SG-R0003", "correction_no": "CR-0003",
                      "original_order_no": "SG-O999999-9"}, headers=H)
check("原单不存在 404", r.status_code, 404)

void_event = {
    "order_no": "SG-V0001", "correction_no": "CR-V001", "order_type": "VOID",
    "original_order_no": "SG-O0002-1", "member_no": "SG-M0002", "store_code": "SH01",
    "amount": 102.0, "ordered_at": "2026-11-15T09:00:00",
}
r = client.post("/orders/corrections", json=void_event, headers=H)
check("撤销投递 201", r.status_code, 201)

late = {
    "order_no": "SG-L0001", "order_type": "LATE_SALE", "member_no": "SG-M0003",
    "store_code": "SH01", "amount": 88.0, "ordered_at": "2026-11-20T09:00:00",
}
r = client.post("/orders/corrections", json=late, headers=H)
check("迟到单投递 201", r.status_code, 201)

print("== 3. 两个 as-of 分群（broker 不可用同步回退）==")
r = client.post("/segments/runs", json={"k": 3, "as_of": "2026-11-12"}, headers=H)
check("基线批次创建", r.status_code, 201)
base_id = r.json()["run"]["id"]
r = client.post("/segments/runs", json={"k": 3, "as_of": "2026-11-30"}, headers=H)
check("更正批次创建", r.status_code, 201)
cur_id = r.json()["run"]["id"]

r = client.get(f"/segments/runs/{base_id}", headers=H).json()["run"]
check("基线批次完成", r["status"], "completed")
check("基线无退款冲减", r["refund_gmv_total"], 0.0)
check("基线含台账快照", r["order_ledger"]["effective_orders"], 360)
cur = client.get(f"/segments/runs/{cur_id}", headers=H).json()["run"]
check("更正批次完成", cur["status"], "completed")
check("更正批次有退款冲减", cur["refund_gmv_total"], 40.0)
check("更正批次净 GMV 低于毛 GMV", cur["gmv_total"] < cur["gross_gmv_total"], True)

print("== 4. 对账接口（五张表齐）==")
r = client.get(f"/reconciliation?current_run_id={cur_id}&baseline_run_id={base_id}", headers=H)
check("对账 200", r.status_code, 200)
body = r.json()
check("快照与台账对平", body["consistency"]["net_gmv_matches"], True)
check("含原单视角", len(body["order_reconciliation"]["sales"]) >= 360, True)
check("含更正视角", len(body["order_reconciliation"]["corrections"]), 2)
check("含会员消费", len(body["member_consumption"]), 120)
check("含分群人数差异", len(body["cluster_differences"]), 3)
check("含预算差异", len(body["budget_differences"]["allocations"]), 3)
m1 = next(x for x in body["member_consumption"] if x["member_no"] == "SG-M0001")
check("M1 净消费核对", m1["net_amount"], 263.0)
check("M1 退款核对", m1["refund_amount"], 40.0)
m2 = next(x for x in body["member_consumption"] if x["member_no"] == "SG-M0002")
check("M2 撤销核对", m2["void_amount"], 102.0)
# viewer 也能读对账（只读权限）
r = client.get(f"/reconciliation?current_run_id={cur_id}", headers=HV)
check("viewer 可只读对账", r.status_code, 200)

print("== 5. 更正流水与订单摘要 as-of ==")
r = client.get("/reconciliation/corrections", headers=H)
check("更正流水 200", r.status_code, 200)
check("流水条数", r.json()["total"], 2)
r = client.get("/reconciliation/corrections?kind=REFUND", headers=H)
check("按类型过滤退款", all(i["order_type"] == "REFUND" for i in r.json()["items"]), True)

r = client.get("/orders/summary?as_of=2026-11-12", headers=H).json()
check("基线摘要有效订单", r["totals"]["orders"], 360)
check("基线摘要无退款", r["correction_overview"]["refund_amount"], 0.0)
r = client.get("/orders/summary?as_of=2026-11-30", headers=H).json()
check("更正摘要净 GMV 与批次一致", r["totals"]["gmv"], cur["gmv_total"])
check("更正摘要退款数", r["correction_overview"]["refund_amount"], 40.0)

print("== 6. 名单接口不含退款/撤销单，会员按净口径在列 ==")
r = client.get(f"/segments/runs/{cur_id}/members?cluster_key={cur['clusters'][0]['cluster_key']}&page_size=100",
               headers=H)
check("名单接口 200", r.status_code, 200)
rows = r.json()["items"]
check("名单行都是会员（无更正单号）", all(str(x["member_no"]).startswith("SG-M") for x in rows), True)
check("名单行带净/毛/退款字段",
      all("gross_monetary" in x and "refunded_monetary" in x for x in rows), True)

with TestingSession() as db:
    check("库内更正事件仅 2 条（重复未入库）",
          db.scalar(select(func.count()).select_from(OrderFact)
                    .where(OrderFact.order_type.in_(["REFUND", "VOID"]))), 2)

print("ALL API E2E TESTS PASSED", file=sys.stderr)
