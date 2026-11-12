-- POS 补传退款 / 撤销 / 迟到订单：order_facts 由「只存销售单」扩展为事件溯源事实表。
-- 幂等 DDL：可重复执行；历史销售行默认 ORDER_TYPE='SALE'，不改变既有分群口径。
ALTER TABLE order_facts
    ADD COLUMN IF NOT EXISTS order_type        VARCHAR(16) NOT NULL DEFAULT 'SALE',
    ADD COLUMN IF NOT EXISTS original_order_no VARCHAR(24),
    ADD COLUMN IF NOT EXISTS correction_no     VARCHAR(32),
    ADD COLUMN IF NOT EXISTS received_at       TIMESTAMPTZ NOT NULL DEFAULT now();

-- 更正单的门店幂等键：同一笔更正重复投递只能落一条、只生效一次
CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_correction_no
    ON order_facts(correction_no)
    WHERE correction_no IS NOT NULL;
-- 按原单反查更正链路（对账页「原单 → 冲减」）
CREATE INDEX IF NOT EXISTS idx_orders_original ON order_facts(original_order_no);
CREATE INDEX IF NOT EXISTS idx_orders_type ON order_facts(order_type);

-- 防御性约束（仅在尚未被脏数据占用时创建）：更正单必须引用原单且类型合法
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_orders_type'
    ) THEN
        ALTER TABLE order_facts
            ADD CONSTRAINT ck_orders_type
            CHECK (order_type IN ('SALE', 'LATE_SALE', 'REFUND', 'VOID'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_orders_correction_ref'
    ) THEN
        ALTER TABLE order_facts
            ADD CONSTRAINT ck_orders_correction_ref
            CHECK (
                (order_type IN ('REFUND', 'VOID')) = (original_order_no IS NOT NULL)
            );
    END IF;
END $$;
