<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div class="card budget-card">
      <div class="budget-header">
        <label for="budget-slider" class="budget-label">{{ t('restocking.budgetLabel') }}</label>
        <div class="budget-value">{{ currencySymbol }}{{ budget.toLocaleString() }}</div>
      </div>
      <input
        id="budget-slider"
        type="range"
        min="0"
        max="5000"
        step="50"
        v-model.number="budget"
        class="budget-slider"
      />
      <div class="budget-range-labels">
        <span>{{ currencySymbol }}0</span>
        <span>{{ currencySymbol }}5,000</span>
      </div>
    </div>

    <div v-if="orderSuccess" class="success-banner">
      <div class="success-title">{{ t('restocking.success.title') }}</div>
      <div class="success-details">
        <span>{{ t('restocking.success.orderNumber') }}: <strong>{{ orderSuccess.order_number }}</strong></span>
        <span>{{ t('restocking.success.total') }}: <strong>{{ currencySymbol }}{{ orderSuccess.total_value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }}</strong></span>
        <span>{{ t('restocking.success.leadTime') }}: <strong>{{ t('restocking.leadTimeDays', { count: orderSuccess.lead_time_days }) }}</strong></span>
        <span>{{ t('restocking.success.expectedDelivery') }}: <strong>{{ formatDate(orderSuccess.expected_delivery) }}</strong></span>
      </div>
    </div>

    <div v-if="orderError" class="error">{{ orderError }}</div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else>
      <div class="stats-grid">
        <div class="stat-card info">
          <div class="stat-label">{{ t('restocking.summary.budget') }}</div>
          <div class="stat-value">{{ currencySymbol }}{{ recommendationsData.budget.toLocaleString() }}</div>
        </div>
        <div class="stat-card warning">
          <div class="stat-label">{{ t('restocking.summary.totalCost') }}</div>
          <div class="stat-value">{{ currencySymbol }}{{ recommendationsData.total_cost.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }}</div>
        </div>
        <div class="stat-card success">
          <div class="stat-label">{{ t('restocking.summary.remainingBudget') }}</div>
          <div class="stat-value">{{ currencySymbol }}{{ recommendationsData.remaining_budget.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }}</div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.recommendations') }} ({{ recommendations.length }})</h3>
          <button
            class="btn-primary"
            :disabled="submitting || recommendations.length === 0"
            @click="placeOrder"
          >
            {{ submitting ? t('restocking.submitting') : t('restocking.placeOrder') }}
          </button>
        </div>

        <div v-if="recommendations.length === 0" class="empty-state">
          {{ t('restocking.emptyState') }}
        </div>
        <div v-else class="table-container">
          <table>
            <thead>
              <tr>
                <th>{{ t('restocking.table.item') }}</th>
                <th>{{ t('restocking.table.category') }}</th>
                <th>{{ t('restocking.table.trend') }}</th>
                <th>{{ t('restocking.table.currentSupply') }}</th>
                <th>{{ t('restocking.table.forecastedDemand') }}</th>
                <th>{{ t('restocking.table.recommendedQty') }}</th>
                <th>{{ t('restocking.table.unitCost') }}</th>
                <th>{{ t('restocking.table.lineCost') }}</th>
                <th>{{ t('restocking.table.leadTime') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="rec in recommendations" :key="rec.item_sku">
                <td>
                  <div class="item-name">{{ rec.item_name }}</div>
                  <div class="item-sku">{{ rec.item_sku }}</div>
                </td>
                <td>{{ translateCategory(rec.category) }}</td>
                <td>
                  <span :class="['badge', getTrendClass(rec.trend)]">
                    {{ t(`trends.${rec.trend}`) }}
                  </span>
                </td>
                <td>{{ rec.current_supply }}</td>
                <td>{{ rec.forecasted_demand }}</td>
                <td>
                  <strong>{{ rec.recommended_quantity }}</strong>
                  <span v-if="rec.clipped" class="partial-hint">{{ t('restocking.partialFillHint') }}</span>
                </td>
                <td>{{ currencySymbol }}{{ rec.unit_cost.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }}</td>
                <td>{{ currencySymbol }}{{ rec.line_cost.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }}</td>
                <td>{{ t('restocking.leadTimeDays', { count: rec.lead_time_days }) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency, currentLocale } = useI18n()

    const currencySymbol = computed(() => {
      return currentCurrency.value === 'JPY' ? '¥' : '$'
    })

    const budget = ref(2000)
    const loading = ref(true)
    const error = ref(null)
    const recommendationsData = ref({ budget: 0, total_cost: 0, remaining_budget: 0, recommendations: [] })
    const submitting = ref(false)
    const orderSuccess = ref(null)
    const orderError = ref(null)

    const recommendations = computed(() => recommendationsData.value.recommendations || [])

    let debounceTimer = null

    const loadRecommendations = async () => {
      try {
        loading.value = true
        error.value = null
        recommendationsData.value = await api.getRestockRecommendations(budget.value)
      } catch (err) {
        error.value = t('restocking.error')
        console.error('Failed to load restock recommendations:', err)
      } finally {
        loading.value = false
      }
    }

    // Debounce API calls while the slider is being dragged, so we don't
    // flood the backend with a request on every intermediate value.
    const scheduleLoad = () => {
      orderSuccess.value = null
      orderError.value = null
      if (debounceTimer) clearTimeout(debounceTimer)
      debounceTimer = setTimeout(() => {
        loadRecommendations()
      }, 300)
    }

    const getTrendClass = (trend) => {
      // Restocking view uses a different color mapping than Demand view:
      // increasing demand needs attention (warning), decreasing is favorable (success)
      const trendMap = {
        increasing: 'warning',
        stable: 'info',
        decreasing: 'success'
      }
      return trendMap[trend] || 'info'
    }

    const translateCategory = (category) => {
      const key = category ? category.charAt(0).toLowerCase() + category.slice(1).replace(/\s+/g, '') : ''
      return t(`categories.${key}`) !== `categories.${key}` ? t(`categories.${key}`) : category
    }

    const formatDate = (dateString) => {
      const date = new Date(dateString)
      if (isNaN(date.getTime())) return dateString
      const locale = currentLocale.value === 'ja' ? 'ja-JP' : 'en-US'
      return date.toLocaleDateString(locale, {
        year: 'numeric',
        month: 'short',
        day: 'numeric'
      })
    }

    const placeOrder = async () => {
      if (recommendations.value.length === 0) return
      submitting.value = true
      orderError.value = null
      orderSuccess.value = null
      try {
        // If a budget-slider debounce is still pending, the displayed
        // recommendations may be stale relative to the current budget.
        // Cancel the pending timer and refresh now so the submitted items
        // match what the user actually sees, and so we don't also trigger
        // a second, racing loadRecommendations() call from the timer.
        if (debounceTimer) {
          clearTimeout(debounceTimer)
          debounceTimer = null
          await loadRecommendations()
        }
        const items = recommendations.value.map(rec => ({
          sku: rec.item_sku,
          quantity: rec.recommended_quantity
        }))
        const order = await api.createRestockOrder(items)
        orderSuccess.value = order
        await loadRecommendations()
      } catch (err) {
        orderError.value = t('restocking.orderError')
        console.error('Failed to place restock order:', err)
      } finally {
        submitting.value = false
      }
    }

    onMounted(loadRecommendations)

    watch(budget, scheduleLoad)

    onBeforeUnmount(() => {
      if (debounceTimer) clearTimeout(debounceTimer)
    })

    return {
      t,
      budget,
      loading,
      error,
      recommendationsData,
      recommendations,
      submitting,
      orderSuccess,
      orderError,
      currencySymbol,
      getTrendClass,
      translateCategory,
      formatDate,
      placeOrder
    }
  }
}
</script>

<style scoped>
.budget-card {
  padding: 1.5rem;
}

.budget-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 1rem;
}

.budget-label {
  font-size: 0.875rem;
  font-weight: 600;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.budget-value {
  font-size: 1.875rem;
  font-weight: 700;
  color: #0f172a;
  letter-spacing: -0.025em;
}

.budget-slider {
  width: 100%;
  height: 6px;
  border-radius: 3px;
  background: #e2e8f0;
  appearance: none;
  outline: none;
  cursor: pointer;
}

.budget-slider::-webkit-slider-thumb {
  appearance: none;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #2563eb;
  cursor: pointer;
  border: 3px solid white;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
}

.budget-slider::-moz-range-thumb {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #2563eb;
  cursor: pointer;
  border: 3px solid white;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
}

.budget-range-labels {
  display: flex;
  justify-content: space-between;
  margin-top: 0.5rem;
  font-size: 0.813rem;
  color: #64748b;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.btn-primary {
  background: #2563eb;
  color: white;
  border: none;
  padding: 0.625rem 1.25rem;
  border-radius: 6px;
  font-size: 0.875rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s ease;
}

.btn-primary:hover:not(:disabled) {
  background: #1d4ed8;
}

.btn-primary:disabled {
  background: #cbd5e1;
  cursor: not-allowed;
}

.item-name {
  font-weight: 500;
  color: #0f172a;
}

.item-sku {
  font-size: 0.75rem;
  color: #64748b;
}

.partial-hint {
  display: block;
  font-size: 0.75rem;
  color: #ea580c;
  font-weight: 500;
}

.empty-state {
  text-align: center;
  padding: 3rem;
  color: #64748b;
  font-size: 0.938rem;
}

.success-banner {
  background: #d1fae5;
  border: 1px solid #6ee7b7;
  color: #065f46;
  padding: 1rem 1.25rem;
  border-radius: 8px;
  margin-bottom: 1.25rem;
}

.success-title {
  font-weight: 700;
  margin-bottom: 0.5rem;
}

.success-details {
  display: flex;
  flex-wrap: wrap;
  gap: 1.25rem;
  font-size: 0.875rem;
}
</style>
