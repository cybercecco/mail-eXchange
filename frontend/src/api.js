const apiBase = "/api";
export const TOKEN_KEY = "mx_access_token";

export function getToken() {
  return sessionStorage.getItem(TOKEN_KEY);
}

function formatHttpErrorBody(body, status, fallback) {
  if (typeof body !== "string") return null;
  const trimmed = body.trim();
  if (!trimmed) return fallback;
  if (!/^<!doctype html|^<html[\s>]/i.test(trimmed)) return trimmed;
  const title = trimmed.match(/<title>([^<]*)<\/title>/i)?.[1]?.trim();
  if (title) return `${title} (${status})`;
  return `${fallback} (${status})`;
}

export function formatApiErrorDetail(body, fallback = "Request failed") {
  if (!body) return fallback;
  const detail = body.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === "string") return item;
        const loc = Array.isArray(item.loc) ? `${item.loc.join(".")}: ` : "";
        return `${loc}${item.msg || JSON.stringify(item)}`;
      })
      .join("\n");
  }
  if (detail && typeof detail === "object") {
    return JSON.stringify(detail, null, 2);
  }
  return JSON.stringify(body, null, 2);
}

export function setToken(token) {
  if (token) {
    sessionStorage.setItem(TOKEN_KEY, token);
  } else {
    sessionStorage.removeItem(TOKEN_KEY);
  }
}

async function readResponseBody(response) {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export async function api(path, options = {}) {
  const { publicAuth, ...fetchOptions } = options;
  const headers = { "Content-Type": "application/json", ...(fetchOptions.headers || {}) };
  const token = getToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  const response = await fetch(`${apiBase}${path}`, { ...fetchOptions, headers });
  if (response.status === 401) {
    if (token && !publicAuth) {
      setToken(null);
      const err = new Error("Session expired");
      err.unauthorized = true;
      throw err;
    }
  }
  if (!response.ok) {
    const body = await readResponseBody(response);
    const detail =
      typeof body === "string"
        ? formatHttpErrorBody(body, response.status, "Request failed")
        : formatApiErrorDetail(body, "Request failed");
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  const body = await readResponseBody(response);
  return body;
}

export async function apiUpload(path, formData) {
  const token = getToken();
  const headers = {};
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  const response = await fetch(`${apiBase}${path}`, { method: "POST", headers, body: formData });
  if (response.status === 401 && token) {
    setToken(null);
    const err = new Error("Session expired");
    err.unauthorized = true;
    throw err;
  }
  if (!response.ok) {
    const body = await readResponseBody(response);
    const detail =
      typeof body === "string"
        ? formatHttpErrorBody(body, response.status, "Upload failed")
        : formatApiErrorDetail(body, "Upload failed");
    throw new Error(detail);
  }
  return readResponseBody(response);
}
