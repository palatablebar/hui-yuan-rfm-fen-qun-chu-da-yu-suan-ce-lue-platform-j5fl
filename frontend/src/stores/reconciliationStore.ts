import { defineStore } from 'pinia'
import {
  fetchCorrectionStream,
  fetchReconciliation,
  ingestCorrection,
} from '../api/order'
import type {
  CorrectionIngestPayload,
  CorrectionStream,
  Reconciliation,
} from '../types/reconciliation'

interface ReconciliationState {
  data: Reconciliation | null
  stream: CorrectionStream | null
  loading: boolean
  submitting: boolean
  error: string
  notice: string
}

export const useReconciliationStore = defineStore('reconciliation', {
  state: (): ReconciliationState => ({
    data: null,
    stream: null,
    loading: false,
    submitting: false,
    error: '',
    notice: '',
  }),
  getters: {
    orderRows: (state) => state.data?.order_reconciliation.sales ?? [],
    correctionRows: (state) => state.data?.order_reconciliation.corrections ?? [],
    memberRows: (state) => state.data?.member_consumption ?? [],
    clusterRows: (state) => state.data?.cluster_differences ?? [],
    budgetRows: (state) => state.data?.budget_differences.allocations ?? [],
  },
  actions: {
    async load(params: { current_run_id?: number; baseline_run_id?: number; total_budget?: number } = {}) {
      this.loading = true
      this.error = ''
      try {
        this.data = await fetchReconciliation(params)
        return this.data
      } catch (error) {
        this.error = (error as { message?: string }).message ?? '加载对账数据失败'
        throw error
      } finally {
        this.loading = false
      }
    },
    async loadStream(params: { as_of?: string; kind?: string } = {}) {
      try {
        this.stream = await fetchCorrectionStream(params)
        return this.stream
      } catch (error) {
        this.error = (error as { message?: string }).message ?? '加载更正流水失败'
        throw error
      }
    },
    async submitCorrection(payload: CorrectionIngestPayload) {
      this.submitting = true
      this.error = ''
      this.notice = ''
      try {
        const result = await ingestCorrection(payload)
        this.notice = result.duplicate
          ? `更正 ${result.item.correction_no ?? ''} 已存在，重复投递未二次生效`
          : `已接收 ${result.item.order_type} 事件 ${result.item.order_no}`
        return result
      } catch (error) {
        this.error = (error as { message?: string }).message ?? '更正投递失败'
        throw error
      } finally {
        this.submitting = false
      }
    },
    clearNotice() {
      this.notice = ''
      this.error = ''
    },
  },
})
