async function request(path, options) {
  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || `请求失败（HTTP ${res.status}）`);
    err.status = res.status;
    throw err;
  }
  return data;
}

const jsonHeaders = { "Content-Type": "application/json" };

export function fetchState() {
  return request("/api/state");
}

export function postPreview(design) {
  return request("/api/preview", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify(design),
  });
}

export function postConfirm(token, design) {
  return request("/api/confirm", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ token, ...design }),
  });
}
