/** Vue 前端唯一 HTTP 客户端；组件不直接使用 fetch。 */
export async function api(path, options = {}) {
  let response
  try {
    response = await fetch(path, options)
  } catch (error) {
    throw new Error(`网络请求失败：${error.message || '无法连接后端'}`)
  }

  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = body.detail || JSON.stringify(body)
    } catch {
      detail = await response.text() || detail
    }
    throw new Error(detail || `请求失败（HTTP ${response.status}）`)
  }
  return response.json()
}
