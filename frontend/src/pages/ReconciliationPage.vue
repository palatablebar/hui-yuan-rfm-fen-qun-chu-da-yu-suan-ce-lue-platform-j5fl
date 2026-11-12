<template>
  <div class="page">
    <PageHeader
      eyebrow="reconciliation · 更正台账对账"
      title="POS 补传更正与会员口径对账"
      lede="只认统计日及之前的有效订单：退款只冲减金额、撤销整笔剔除、迟到单按业务发生时点采纳，同一更正重复投递只生效一次。原单、冲减、会员消费、分群人数与预算差异在同一页对平。"
    >
      <template #actions>
        <RunSelector :runs="segmentStore.runs" :model-value="currentRunId" @update:model-value="selectCurrentRun" />
        <select v-model.number="baselineRunId" class="select baseline-select" data-testid="baseline-select">
          <option :value="null">无基线批次</option>
          <option v-for="run in baselineOptions" :key="run.id" :value="run.id">
            基线 {{ run.run_no }} · {{ run.as_of }}
          </option>
        </select>
        <button class="btn btn-sm" type="button" :disabled="reconStore.loading" @click="reload">重新核对</button>
      </template>
    </PageHeader>

    <div v-if="reconStore.notice" class="notice ok" style="margin-bottom: 16px">{{ reconStore.notice }}</div>
    <ErrorNotice v-if="reconStore.error" :message="reconStore.error" style="margin-bottom: 16px" @retry="reload" />

    <template v-if="data">
      <!-- 口径一致性 KPI -->
      <div class="kpi-strip" data-testid="recon-kpis">
        <div class="kpi">
          <div class="kpi-label">统计日 as-of</div>
          <div class="kpi-value compact">{{ data.as_of }}</div>
          <div class="kpi-foot">{{ data.current_run.run_no }} · k={{ data.current_run.selected_k ?? '—' }}</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">有效订单 / 原口径销售</div>
          <div class="kpi-value">{{ formatInt(ledger.effective_orders) }}<small>/ {{ formatInt(ledger.sale_orders) }}</small></div>
          <div class="kpi-foot">撤销 {{ formatInt(ledger.voided_orders) }} · 统计日后 {{ formatInt(ledger.after_cutoff_sales) }}</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">原口径 GMV → 净 GMV</div>
          <div class="kpi-value compact">{{ formatMoney(ledger.net_gmv) }}</div>
          <div class="kpi-foot">毛 {{ formatMoney(ledger.gross_gmv) }} · 退 {{ formatMoney(ledger.refund_amount) }} · 撤 {{ formatMoney(ledger.void_amount) }}</div>
        </div>
        <div class="kpi">
          <div class="kpi-label">采纳更正 / 重复拦截</div>
          <div class="kpi-value">{{ formatInt(ledger.correction_applied) }}<small>/ {{ formatInt(ledger.correction_duplicate_total) }}</small></div>
          <div class="kpi-foot">退款 {{ formatInt(ledger.correction_refund_total) }} · 撤销 {{ formatInt(ledger.correction_void_total) }}</div>
        </div>
        <div class="kpi" :class="{ mismatch: !data.consistency.net_gmv_matches }">
          <div class="kpi-label">快照 vs 台账复算</div>
          <div class="kpi-value compact">{{ data.consistency.net_gmv_matches ? '对平' : '不一致' }}</div>
          <div class="kpi-foot">
            快照 {{ formatMoney(data.consistency.snapshot_net_gmv) }} · 台账 {{ formatMoney(data.consistency.ledger_net_gmv) }}
          </div>
        </div>
      </div>

      <!-- POS 补传录入 -->
      <StrataPanel v-if="auth.canRunSegment" eyebrow="pos ingest · 补传退款 / 撤销 / 迟到单" style="margin-top: 20px">
        <template #meta>
          <span class="muted">correction_no 为幂等键，重复投递只生效一次</span>
        </template>
        <form class="ingest-grid" @submit.prevent="submitIngest">
          <label class="field">
            <span>事件类型</span>
            <select v-model="form.order_type" class="select" data-testid="ingest-type">
              <option value="REFUND">退款单（冲减金额）</option>
              <option value="VOID">撤销单（整笔作废）</option>
              <option value="LATE_SALE">迟到补传销售单</option>
              <option value="SALE">销售单</option>
            </select>
          </label>
          <label class="field">
            <span>事件单号 order_no</span>
            <input v-model="form.order_no" class="input" required placeholder="SG-O900xxx" />
          </label>
          <label class="field">
            <span>幂等键 correction_no{{ isCorrection ? '（必填）' : '（可选）' }}</span>
            <input v-model="form.correction_no" class="input" :required="isCorrection" placeholder="CR-xxxx" />
          </label>
          <label class="field">
            <span>原销售单号{{ isCorrection ? '（必填）' : '' }}</span>
            <input v-model="form.original_order_no" class="input" :required="isCorrection" placeholder="SG-O000xxx" />
          </label>
          <label class="field">
            <span>会员编号</span>
            <input v-model="form.member_no" class="input" required placeholder="SG-M0001" />
          </label>
          <label class="field">
            <span>门店</span>
            <input v-model="form.store_code" class="input" required placeholder="SH01" />
          </label>
          <label class="field">
            <span>金额（元，正数）</span>
            <input v-model.number="form.amount" class="input" type="number" min="0" step="0.01" required />
          </label>
          <label class="field">
            <span>业务发生时间</span>
            <input v-model="form.ordered_at" class="input" type="datetime-local" required />
          </label>
          <div class="ingest-actions">
            <button class="btn btn-accent" type="submit" :disabled="reconStore.submitting" data-testid="ingest-submit">
              {{ reconStore.submitting ? '投递中…' : '投递更正事件' }}
            </button>
            <p class="muted hint">退款/撤销金额按负向冲减落库，原单保留不覆盖；统计日之后的事件不会进入当前 as-of 口径。</p>
          </div>
        </form>
      </StrataPanel>

      <!-- 1. 原单与冲减金额 -->
      <StrataPanel eyebrow="1 · original orders · 原单与冲减金额" style="margin-top: 18px">
        <template #meta>
          <span>显示前 {{ saleRows.length }} / {{ data.order_reconciliation.sales.length }} 单</span>
        </template>
        <div class="scroll-x">
          <table class="data-table" data-testid="sales-table">
            <thead>
              <tr>
                <th>原单号</th>
                <th>类型</th>
                <th>业务日期</th>
                <th class="num">原金额</th>
                <th class="num">退款冲减</th>
                <th class="num">净金额</th>
                <th>状态</th>
                <th>撤销依据</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in saleRows" :key="row.order_no" :class="{ dim: !row.within_cutoff || row.voided }">
                <td class="mono-num">{{ row.order_no }}</td>
                <td>{{ typeLabel(row.order_type) }}</td>
                <td class="muted">{{ row.ordered_at }}</td>
                <td class="num">{{ formatMoney(row.gross_amount) }}</td>
                <td class="num reversal">{{ row.refunded_amount ? formatMoney(row.refunded_amount) : '—' }}</td>
                <td class="num strong">{{ row.within_cutoff && !row.voided ? formatMoney(row.net_amount) : '—' }}</td>
                <td><span class="tag" :data-tone="statusTone(row.status)">{{ statusLabel(row.status) }}</span></td>
                <td class="mono-num muted">{{ row.void_correction_no ?? '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </StrataPanel>

      <!-- 2. 更正事件（含未采纳原因）-->
      <StrataPanel eyebrow="2 · corrections · 更正事件与采纳情况" style="margin-top: 18px">
        <template #meta>
          <span>共 {{ data.order_reconciliation.corrections.length }} 条 · 采纳 {{ ledger.correction_applied }} 条</span>
        </template>
        <div class="scroll-x">
          <table class="data-table" data-testid="corrections-table">
            <thead>
              <tr>
                <th>更正单号</th>
                <th>幂等键</th>
                <th>类型</th>
                <th>引用原单</th>
                <th class="num">冲减金额</th>
                <th>业务日期</th>
                <th>采纳</th>
                <th>未采纳原因 / 依据</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in data.order_reconciliation.corrections" :key="`${row.correction_no}-${row.order_no}`" :class="{ dim: !row.applied }">
                <td class="mono-num">{{ row.order_no }}</td>
                <td class="mono-num">{{ row.correction_no }}</td>
                <td>{{ typeLabel(row.order_type) }}</td>
                <td class="mono-num">{{ row.original_order_no ?? '—' }}</td>
                <td class="num reversal">{{ formatMoney(row.reversal_amount) }}</td>
                <td class="muted">{{ row.ordered_at }}</td>
                <td>
                  <span class="tag" :data-tone="row.applied ? 'ok' : 'danger'">
                    {{ row.applied ? '已采纳' : '未采纳' }}
                  </span>
                </td>
                <td class="muted">{{ row.applied ? '—' : reasonLabel(row.not_applied_reason) }}</td>
              </tr>
              <tr v-if="!data.order_reconciliation.corrections.length">
                <td colspan="8" class="muted">当前统计日口径下没有更正事件。</td>
              </tr>
            </tbody>
          </table>
        </div>
      </StrataPanel>

      <!-- 3. 会员消费核对 -->
      <StrataPanel eyebrow="3 · member consumption · 会员消费（原口径 / 冲减 / 净口径）" style="margin-top: 18px">
        <template #meta>
          <span>仅展示有冲减或分群迁移的会员，前 {{ memberRows.length }} 人</span>
        </template>
        <div class="scroll-x">
          <table class="data-table" data-testid="member-consumption-table">
            <thead>
              <tr>
                <th>会员编号</th>
                <th>姓名</th>
                <th class="num">订单/有效</th>
                <th class="num">原累计消费</th>
                <th class="num">退款冲减</th>
                <th class="num">撤销冲减</th>
                <th class="num">净消费 M</th>
                <th>基线簇 → 当前簇</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in memberRows" :key="row.member_id" :class="{ migrated: row.cluster_changed }">
                <td class="mono-num">{{ row.member_no }}</td>
                <td>{{ row.name }}</td>
                <td class="num">{{ row.order_count }} / {{ row.effective_order_count }}</td>
                <td class="num">{{ formatMoney(row.gross_amount) }}</td>
                <td class="num reversal">{{ row.refund_amount ? formatMoney(row.refund_amount) : '—' }}</td>
                <td class="num reversal">{{ row.void_amount ? formatMoney(row.void_amount) : '—' }}</td>
                <td class="num strong">{{ formatMoney(row.net_amount) }}</td>
                <td>
                  <span class="muted">{{ row.baseline_cluster ?? '—' }}</span>
                  <span v-if="row.baseline_cluster"> → </span>
                  <span class="tag" :class="{ migrated: row.cluster_changed }">{{ row.current_cluster ?? '—' }}</span>
                  <span v-if="row.cluster_changed" class="migrate-flag">迁移</span>
                </td>
              </tr>
              <tr v-if="!memberRows.length">
                <td colspan="8" class="muted">没有发生冲减或分群迁移的会员。</td>
              </tr>
            </tbody>
          </table>
        </div>
      </StrataPanel>

      <!-- 4 & 5. 分群人数与预算差异 -->
      <div class="grid-2" style="margin-top: 18px">
        <StrataPanel eyebrow="4 · cluster headcount · 分群人数差异">
          <template #meta>
            <span>{{ data.baseline_run ? `对 ${data.baseline_run.run_no}` : '无基线' }}</span>
          </template>
          <div class="scroll-x">
            <table class="data-table" data-testid="cluster-diff-table">
              <thead>
                <tr>
                  <th>簇</th>
                  <th class="num">基线人数</th>
                  <th class="num">当前人数</th>
                  <th class="num">差异</th>
                  <th class="num">可触达差异</th>
                  <th class="num">GMV 差异</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in data.cluster_differences" :key="row.cluster_key">
                  <td>{{ row.cluster_key }} <span class="muted">{{ row.label }}</span></td>
                  <td class="num">{{ formatInt(row.baseline_member_count) }}</td>
                  <td class="num">{{ formatInt(row.current_member_count) }}</td>
                  <td class="num" :class="deltaClass(row.member_count_delta)">{{ signed(row.member_count_delta) }}</td>
                  <td class="num" :class="deltaClass(row.reachable_delta)">{{ signed(row.reachable_delta) }}</td>
                  <td class="num" :class="deltaClass(row.gmv_delta)">{{ signedMoney(row.gmv_delta) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </StrataPanel>

        <StrataPanel eyebrow="5 · budget delta · 同预算重算差异">
          <template #meta>
            <span>预算 {{ formatMoney(data.budget_differences.total_budget) }}</span>
          </template>
          <div class="scroll-x">
            <table class="data-table" data-testid="budget-diff-table">
              <thead>
                <tr>
                  <th>簇</th>
                  <th class="num">基线预算</th>
                  <th class="num">当前预算</th>
                  <th class="num">差异</th>
                  <th>券面额</th>
                  <th class="num">预计 GMV 差异</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in data.budget_differences.allocations" :key="row.cluster_key">
                  <td>{{ row.cluster_key }}</td>
                  <td class="num">{{ formatMoney(row.baseline_budget) }}</td>
                  <td class="num">{{ formatMoney(row.current_budget) }}</td>
                  <td class="num" :class="deltaClass(row.budget_delta)">{{ signedMoney(row.budget_delta) }}</td>
                  <td class="num">
                    {{ row.baseline_coupon_face ?? '—' }} → {{ row.current_coupon_face ?? '—' }}
                  </td>
                  <td class="num" :class="deltaClass(row.expected_gmv_delta)">{{ signedMoney(row.expected_gmv_delta) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p class="muted footnote">
            当前合计预计核销 {{ data.budget_differences.current_totals?.expected_redemptions ?? '—' }} 张 ·
            预计增量 GMV {{ formatMoney(data.budget_differences.current_totals?.expected_gmv ?? 0) }}。
            退款/撤销单本身不进入任何簇名单，会员按净口径重新分群后再分配预算。
          </p>
        </StrataPanel>
      </div>
    </template>

    <EmptyState
      v-else-if="!reconStore.loading"
      title="暂无可对账的已完成批次"
      text="先在分群总览跑一次分群；POS 补传退款/撤销/迟到单后，再以更晚的 as-of 重跑即可在此核对差异。"
    />

    <p class="core-footer">
      <span>event sourcing · 原单不覆盖</span>
      <span>correction_no 幂等</span>
      <span>净 GMV 快照复算</span>
      <span style="margin-left: auto">as-of {{ data?.as_of ?? '—' }}</span>
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import PageHeader from '../components/common/PageHeader.vue'
import StrataPanel from '../components/common/StrataPanel.vue'
import RunSelector from '../components/common/RunSelector.vue'
import EmptyState from '../components/common/EmptyState.vue'
import ErrorNotice from '../components/common/ErrorNotice.vue'
import { useSegmentStore } from '../stores/segmentStore'
import { useReconciliationStore } from '../stores/reconciliationStore'
import { useAuthStore } from '../stores/authStore'
import {
  NOT_APPLIED_REASON_META,
  ORDER_STATUS_META,
  ORDER_TYPE_META,
  type OrderType,
} from '../constants/orderTypes'
import type { CorrectionIngestPayload, OrderLedgerSummary } from '../types/reconciliation'
import { formatInt, formatMoney } from '../utils/formatNumber'

const EMPTY_LEDGER: OrderLedgerSummary = {
  sale_orders: 0,
  late_sale_orders: 0,
  after_cutoff_sales: 0,
  voided_orders: 0,
  refunded_orders: 0,
  effective_orders: 0,
  gross_gmv: 0,
  refund_amount: 0,
  void_amount: 0,
  net_gmv: 0,
  correction_total: 0,
  correction_applied: 0,
  correction_refund_total: 0,
  correction_void_total: 0,
  correction_duplicate_total: 0,
  correction_after_cutoff_total: 0,
}

const segmentStore = useSegmentStore()
const reconStore = useReconciliationStore()
const auth = useAuthStore()

const currentRunId = ref<number | null>(null)
const baselineRunId = ref<number | null>(null)
const SALE_ROW_LIMIT = 60
const MEMBER_ROW_LIMIT = 60

const emptyForm = (): CorrectionIngestPayload => ({
  order_no: '',
  correction_no: '',
  order_type: 'REFUND',
  original_order_no: '',
  member_no: '',
  store_code: 'SH01',
  amount: 0,
  ordered_at: '2026-11-15T10:00',
  coupon_issued: false,
  coupon_redeemed: false,
  coupon_discount: 0,
})
const form = ref<CorrectionIngestPayload>(emptyForm())

const data = computed(() => reconStore.data)
const ledger = computed<OrderLedgerSummary>(
  () => data.value?.order_reconciliation.summary ?? EMPTY_LEDGER,
)
const isCorrection = computed(() => form.value.order_type === 'REFUND' || form.value.order_type === 'VOID')
const baselineOptions = computed(() =>
  segmentStore.runs.filter((run) => run.status === 'completed' && run.id !== currentRunId.value),
)
// 原单表优先展示有冲减/超期/撤销的行，再补有效单，截断到固定行数
const saleRows = computed(() => {
  const rows = data.value?.order_reconciliation.sales ?? []
  const priority = rows.filter((row) => row.status !== 'effective')
  const rest = rows.filter((row) => row.status === 'effective')
  return [...priority, ...rest].slice(0, SALE_ROW_LIMIT)
})
const memberRows = computed(() => (data.value?.member_consumption ?? []).slice(0, MEMBER_ROW_LIMIT))

function typeLabel(type: string): string {
  return ORDER_TYPE_META[type as OrderType]?.label ?? type
}
function statusLabel(status: string): string {
  return ORDER_STATUS_META[status] ?? status
}
function reasonLabel(reason: string): string {
  return NOT_APPLIED_REASON_META[reason] ?? reason
}
function statusTone(status: string): string {
  if (status === 'voided' || status === 'after_cutoff') return 'danger'
  if (status === 'refunded' || status === 'fully_refunded') return 'warn'
  return 'ok'
}
function deltaClass(value: number): string {
  return value > 0 ? 'delta-up' : value < 0 ? 'delta-down' : ''
}
function signed(value: number): string {
  return value > 0 ? `+${formatInt(value)}` : formatInt(value)
}
function signedMoney(value: number): string {
  const text = formatMoney(Math.abs(value))
  if (value > 0) return `+${text}`
  if (value < 0) return `-${text}`
  return text
}

async function loadRecon() {
  if (!currentRunId.value) return
  try {
    await reconStore.load({
      current_run_id: currentRunId.value,
      baseline_run_id: baselineRunId.value ?? undefined,
    })
  } catch {
    /* store.error 已记录 */
  }
}

async function selectCurrentRun(runId: number) {
  currentRunId.value = runId
  if (baselineRunId.value === runId) baselineRunId.value = null
  await loadRecon()
}

async function reload() {
  await segmentStore.loadRuns()
  await loadRecon()
}

async function submitIngest() {
  const payload: CorrectionIngestPayload = {
    ...form.value,
    correction_no: isCorrection.value ? form.value.correction_no : null,
    original_order_no: isCorrection.value ? form.value.original_order_no : null,
  }
  try {
    await reconStore.submitCorrection(payload)
    form.value = { ...emptyForm(), order_type: form.value.order_type, store_code: form.value.store_code }
    await reload()
  } catch {
    /* store.error 已记录 */
  }
}

onMounted(async () => {
  try {
    await segmentStore.loadRuns()
    const target = segmentStore.latestCompleted?.id ?? segmentStore.runs[0]?.id ?? null
    currentRunId.value = target
    const earlier = segmentStore.runs.find((run) => run.status === 'completed' && run.id !== target)
    baselineRunId.value = earlier?.id ?? null
    if (target) await loadRecon()
  } catch {
    /* handled */
  }
})
</script>

<style scoped>
.kpi-value.compact {
  font-size: var(--step-2);
}
.kpi.mismatch {
  border-color: var(--danger);
}
.baseline-select {
  min-width: 220px;
}
.ingest-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px 14px;
}
.ingest-grid .field span {
  font-size: var(--step--1);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--ink-3);
  margin-bottom: 4px;
  display: block;
}
.ingest-actions {
  grid-column: span 4;
  display: flex;
  align-items: center;
  gap: 14px;
}
.ingest-actions .hint {
  font-size: var(--step--1);
}
.tag[data-tone='ok'] {
  color: var(--ok);
  border-color: color-mix(in oklch, var(--ok) 45%, var(--line-strong));
}
.tag[data-tone='warn'] {
  color: var(--rust);
  border-color: color-mix(in oklch, var(--rust) 40%, var(--line-strong));
}
.tag[data-tone='danger'] {
  color: var(--danger);
  border-color: color-mix(in oklch, var(--danger) 40%, var(--line-strong));
}
.data-table tr.dim {
  opacity: 0.62;
}
.data-table tr.migrated {
  background: color-mix(in oklch, var(--highlight) 28%, transparent);
}
.num.reversal {
  color: var(--rust);
}
.num.strong {
  font-weight: 600;
}
.delta-up {
  color: var(--moss);
}
.delta-down {
  color: var(--rust);
}
.migrate-flag {
  margin-left: 6px;
  font-size: var(--step--1);
  color: var(--rust);
  letter-spacing: 0.08em;
}
.tag.migrated {
  border-color: var(--rust);
  color: var(--rust);
}
.footnote {
  margin-top: 10px;
  font-size: var(--step--1);
}
@media (max-width: 1100px) {
  .ingest-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .ingest-actions {
    grid-column: span 2;
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
