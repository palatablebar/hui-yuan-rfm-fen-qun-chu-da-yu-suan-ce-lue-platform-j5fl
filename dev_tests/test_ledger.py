"""纯逻辑验证：order_ledger 口径 + rfm_engine 端到端（不依赖 DB）。

运行：cd backend && PYTHONPATH=. python3 -I /workspace/dev_tests/test_ledger.py
"""
import sys
from datetime import date, datetime

sys.path.insert(0, "/home/node/.local/lib/python3.11/site-packages")
sys.path.insert(0, "/workspace/backend")

from app.services.order_ledger import build_order_ledger, member_consumption
from app.services import rfm_engine

AS_OF = date(2026, 11, 12)


def ev(order_no, mid, amount, day, order_type="SALE", original=None, correction_no=None, id_=0):
    return {
        "id": id_,
        "order_no": order_no,
        "member_id": mid,
        "store_code": "SH01",
        "amount": amount,
        "ordered_at": datetime(2026, 11, day, 10, 0),
        "coupon_issued": False,
        "coupon_redeemed": False,
        "order_type": order_type,
        "original_order_no": original,
        "correction_no": correction_no,
    }


def check(name, got, want):
    assert got == want, f"{name}: got {got!r}, want {want!r}"
    print(f"  ok  {name} = {got!r}")


def test_ledger_basic():
    print("== test_ledger_basic ==")
    events = [
        ev("S1", 1, 100.0, 1, id_=1),
        ev("S2", 1, 50.0, 2, id_=2),
        ev("S3", 2, 200.0, 3, id_=3),
        # 部分退款 S1 60 元（统计日内）
        ev("R1", 1, -60.0, 5, "REFUND", "S1", "CR-1", id_=4),
        # 同一 correction_no 重复投递（换新 order_no）→ 只生效一次
        ev("R1DUP", 1, -60.0, 5, "REFUND", "S1", "CR-1", id_=5),
        # 撤销 S3
        ev("V1", 2, -200.0, 6, "VOID", "S3", "CR-2", id_=6),
        # 统计日之后的退款 → 不采纳
        ev("R2", 1, -10.0, 20, "REFUND", "S2", "CR-3", id_=7),
        # 统计日之后的迟到销售单 → 不进有效订单
        ev("L1", 2, 300.0, 18, "LATE_SALE", id_=8),
    ]
    ledger = build_order_ledger(events, AS_OF)
    s = ledger["summary"]
    check("sale_orders(<=asof)", s["sale_orders"], 3)
    check("after_cutoff_sales", s["after_cutoff_sales"], 1)
    check("late_sale_orders", s["late_sale_orders"], 1)
    check("voided_orders", s["voided_orders"], 1)
    check("refunded_orders", s["refunded_orders"], 1)
    check("effective_orders", s["effective_orders"], 2)  # S1(净40) + S2(50)
    check("gross_gmv", s["gross_gmv"], 350.0)
    check("refund_amount(去重后)", s["refund_amount"], 60.0)
    check("void_amount", s["void_amount"], 200.0)
    check("net_gmv", s["net_gmv"], 90.0)
    check("correction_total", s["correction_total"], 4)
    check("correction_applied", s["correction_applied"], 2)
    check("correction_duplicate_total", s["correction_duplicate_total"], 1)
    check("correction_after_cutoff_total", s["correction_after_cutoff_total"], 1)

    sales = {row["order_no"]: row for row in ledger["sales"]}
    check("S1 状态", sales["S1"]["status"], "refunded")
    check("S1 净额", sales["S1"]["net_amount"], 40.0)
    check("S3 状态", sales["S3"]["status"], "voided")
    check("L1 状态", sales["L1"]["status"], "after_cutoff")

    corrections = {row["correction_no"] + "|" + row["order_no"]: row for row in ledger["corrections"]}
    check("R1 applied", corrections["CR-1|R1"]["applied"], True)
    check("R1DUP 重复", corrections["CR-1|R1DUP"]["duplicate"], True)
    check("R1DUP 未生效", corrections["CR-1|R1DUP"]["applied"], False)
    check("R2 超期未采纳", corrections["CR-3|R2"]["not_applied_reason"], "after_cutoff")

    consumption = member_consumption(ledger)
    check("会员1 原口径", consumption[1]["gross_amount"], 150.0)
    check("会员1 退款", consumption[1]["refund_amount"], 60.0)
    check("会员1 净口径", consumption[1]["net_amount"], 90.0)
    check("会员1 有效订单数", consumption[1]["effective_order_count"], 2)
    check("会员2 撤销金额", consumption[2]["void_amount"], 200.0)
    check("会员2 净口径为0", consumption[2]["net_amount"], 0.0)


def test_refund_cap_and_voided_refund():
    print("== test_refund_cap_and_voided_refund ==")
    events = [
        ev("S1", 1, 100.0, 1, id_=1),
        ev("V1", 1, -100.0, 2, "VOID", "S1", "CR-V", id_=2),
        # 已撤销原单的退款 → 不采纳
        ev("R1", 1, -50.0, 3, "REFUND", "S1", "CR-R", id_=3),
        ev("S2", 2, 80.0, 1, id_=4),
        # 多笔退款累计超过原单 → 单维封顶 80
        ev("R2", 2, -50.0, 4, "REFUND", "S2", "CR-2", id_=5),
        ev("R3", 2, -40.0, 5, "REFUND", "S2", "CR-3", id_=6),
        # 原单本身在统计日之后，统计日内的更正也无对象可冲减
        ev("S4", 3, 70.0, 20, "LATE_SALE", id_=7),
        ev("R4", 3, -70.0, 11, "REFUND", "S4", "CR-4", id_=8),
    ]
    ledger = build_order_ledger(events, AS_OF)
    check("S1 仍为 voided", {r["order_no"]: r["status"] for r in ledger["sales"]}["S1"], "voided")
    cr = {r["correction_no"]: r for r in ledger["corrections"]}
    check("撤销后退款未采纳", cr["CR-R"]["not_applied_reason"], "original_already_voided")
    check("原单超期的更正未采纳", cr["CR-4"]["not_applied_reason"], "original_after_cutoff")
    check("超期原单退款未生效", cr["CR-4"]["applied"], False)
    s2 = next(r for r in ledger["sales"] if r["order_no"] == "S2")
    check("S2 退款封顶原单金额", s2["refunded_amount"], 80.0)
    check("S2 净额不为负", s2["net_amount"], 0.0)
    check("S2 全额退款状态", s2["status"], "fully_refunded")


def test_rfm_engine_end_to_end():
    print("== test_rfm_engine_end_to_end ==")
    # 120 个会员、每人若干单，制造可复现的退款/撤销场景
    members = [
        {
            "member_id": i,
            "member_no": f"M{i:04d}",
            "name": f"n{i}",
            "store_code": "SH01",
            "store_name": "店",
            "consent": True,
            "joined_at": date(2024, 1, 1),
        }
        for i in range(1, 121)
    ]
    orders = []
    oid = 0
    for i in range(1, 121):
        for d in (1, 4, 8):
            oid += 1
            orders.append(ev(f"S-{i}-{d}", i, 100.0 + i, d, id_=oid))
    # 会员 1 的一笔单全退 → M 降 101，F 不变
    oid += 1
    orders.append(ev("RX", 1, -101.0, 10, "REFUND", "S-1-1", "CRX", id_=oid))
    # 会员 2 的一笔单撤销 → F 减 1、M 减 102
    oid += 1
    orders.append(ev("VX", 2, -102.0, 11, "VOID", "S-2-1", "CRV", id_=oid))
    # 统计日之后事件，不应影响任何口径
    oid += 1
    orders.append(ev("LATE", 3, 999.0, 25, "LATE_SALE", id_=oid))
    oid += 1
    orders.append(ev("RLATE", 3, -103.0, 26, "REFUND", "S-3-1", "CRL", id_=oid))

    result = rfm_engine.run_segmentation(members, orders, as_of=AS_OF, k=3)
    check("结果含 order_ledger", "order_ledger" in result, True)
    check("ledger 净 GMV", result["order_ledger"]["net_gmv"],
          result["gmv_total"])
    assign = {a["member_id"]: a for a in result["assignments"]}
    # 会员1：3 单有效，毛 303、退 101、净 202
    check("会员1 F 不变", assign[1]["frequency"], 3)
    check("会员1 毛消费", assign[1]["gross_monetary"], 303.0)
    check("会员1 退款", assign[1]["refunded_monetary"], 101.0)
    check("会员1 净 M", assign[1]["monetary"], 202.0)
    # 会员2：撤销后只剩 2 单
    check("会员2 F 减1", assign[2]["frequency"], 2)
    check("会员2 净 M", assign[2]["monetary"], 204.0)
    # 会员3：迟到单与超期退款都不采纳，仍是原始 3 单 309
    check("会员3 F 原样", assign[3]["frequency"], 3)
    check("会员3 净 M 原样", assign[3]["monetary"], 309.0)
    check("总有效订单数", result["order_ledger"]["effective_orders"], 120 * 3 - 1)

    # 可复现：同输入再跑一次必须逐字相等
    again = rfm_engine.run_segmentation(members, orders, as_of=AS_OF, k=3)
    check("两次结果可复现", again["assignments"], result["assignments"])
    check("两次簇画像可复现", again["clusters"], result["clusters"])


if __name__ == "__main__":
    test_ledger_basic()
    test_refund_cap_and_voided_refund()
    test_rfm_engine_end_to_end()
    print("ALL PURE-LOGIC TESTS PASSED", file=sys.stderr)
