// Base URL resolution order: runtime-injected config -> build-time env -> same-origin reverse proxy.
const API_BASE_URL =
  runtimeConfig().apiUrl || import.meta.env.VITE_API_URL || '/api'

// 请求超时（毫秒）。
// 不加超时的话，LLM 或依赖服务卡住时 fetch 会一直挂着，
// 界面永远停在「处理中」，用户既拿不到结果也没法取消。
// /chat 实测单次约 18 秒（要跑意图识别 + 知识检索 + Agent 编排），
// 评测会串行跑多组用例，这两类单独放宽；其余交互类接口 30 秒足够。
const DEFAULT_TIMEOUT_MS = 30_000
const CHAT_TIMEOUT_MS = 90_000
const EVAL_TIMEOUT_MS = 180_000

export function createInitialSettings() {
  const saved = readSettings()
  return {
    userId: saved.userId || 'u1001',
    conversationId: saved.conversationId || ''
  }
}

export function saveSettings(settings) {
  localStorage.setItem('echomind.frontend.settings', JSON.stringify(settings))
}

export function apiBaseUrl() {
  return normalizeBaseUrl(API_BASE_URL)
}

export function requestHealth() {
  return requestJson('/health')
}

export function requestMonitor() {
  return requestJson('/monitor')
}

export function requestSkills() {
  return requestJson('/skills')
}

export function reloadSkills() {
  return requestJson('/skills/reload', { method: 'POST' })
}

export function requestKnowledgeStats() {
  return requestJson('/knowledge/stats')
}

export function runEvaluation(body = null) {
  return requestJson('/eval/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
    timeout: EVAL_TIMEOUT_MS
  })
}

export function requestSearch(query, topK = 5) {
  const params = new URLSearchParams({ query, top_k: String(topK) })
  return requestJson(`/search?${params}`, { method: 'POST' })
}

export async function requestChat(settings, message) {
  const raw = await requestJson('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message,
      user_id: settings.userId || 'anonymous',
      conv_id: settings.conversationId || undefined
    }),
    timeout: CHAT_TIMEOUT_MS
  })
  return normalizeChatResponse(raw)
}

export async function requestToolTrace(requestId) {
  if (!requestId) return null
  const raw = await requestJson(`/trace/tool/${encodeURIComponent(requestId)}`)
  return normalizeToolTraceResponse(raw)
}

export function addKnowledge(documents) {
  return requestJson('/knowledge/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ documents })
  })
}

export function uploadKnowledge(file) {
  const form = new FormData()
  form.append('file', file)
  return requestJson('/knowledge/upload', { method: 'POST', body: form })
}

function normalizeChatResponse(raw) {
  return {
    conversationId: raw.conv_id || '',
    requestId: raw.request_id || '',
    response: raw.response || '',
    intent: raw.intent || 'other',
    intentGroup: raw.intent_group || 'other',
    agentType: raw.agent_type || '',
    agentTypes: raw.agent_types || [],
    primaryAgent: raw.primary_agent || '',
    supportingAgents: raw.supporting_agents || [],
    routingReason: raw.routing_reason || '',
    routingConfidence: Number(raw.routing_confidence ?? 0),
    entities: raw.entities || {},
    intentConfidence: Number(raw.intent_confidence ?? 0),
    intentSourceScores: raw.intent_source_scores || {},
    escalated: Boolean(raw.escalated),
    latencyMs: Number(raw.latency_ms ?? 0),
    knowledgeUsed: Boolean(raw.knowledge_used),
    raw
  }
}

function normalizeToolTraceResponse(raw) {
  const trace = raw?.trace || {}
  return {
    requestId: raw?.request_id || '',
    found: Boolean(raw?.found),
    trace: {
      ...trace,
      toolsUsed: trace.tools_used || [],
      toolCalls: trace.tool_calls || []
    },
    raw
  }
}

async function requestJson(path, options = {}) {
  const { timeout = DEFAULT_TIMEOUT_MS, ...fetchOptions } = options
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)

  let response
  let text
  try {
    response = await fetch(`${apiBaseUrl()}${path}`, {
      ...fetchOptions,
      signal: controller.signal
    })
    // 响应体读取同样受超时约束：连接已建立但服务端迟迟不吐数据时，
    // 若只给 fetch 加 signal、不给 text() 兜底，依然会无限等待。
    text = await response.text()
  } catch (error) {
    if (error?.name === 'AbortError') {
      throw new Error(`请求超时：${Math.round(timeout / 1000)} 秒内没有收到响应`)
    }
    throw error
  } finally {
    clearTimeout(timer)
  }

  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!response.ok) {
    const detail = typeof data === 'string' ? data : JSON.stringify(data)
    throw new Error(`${response.status} ${response.statusText}: ${detail}`)
  }
  return data
}

function normalizeBaseUrl(value) {
  return String(value || '').replace(/\/+$/, '')
}

function readSettings() {
  try {
    return JSON.parse(localStorage.getItem('echomind.frontend.settings') || '{}')
  } catch {
    return {}
  }
}

function runtimeConfig() {
  if (typeof window === 'undefined') return {}
  return window.__ECHOMIND_CONFIG__ || {}
}
