const apiToken = import.meta.env.VITE_WH_API_TOKEN || "dev-admin-token";

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      Authorization: `Bearer ${apiToken}`,
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") message = payload.detail;
    } catch {
      // Keep the status-based fallback when the response is not JSON.
    }
    throw new Error(message);
  }

  return response.json() as Promise<T>;
}
