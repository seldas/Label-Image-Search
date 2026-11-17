export async function fetchStatus() {
  const res = await fetch("/api/status");
  if (!res.ok) throw new Error("Failed to fetch system status");
  return res.json();
}

export async function searchImages({ text, imageFile, useAi = true }) {
  const form = new FormData();
  if (text && text.trim()) form.append("text", text.trim());
  if (imageFile) form.append("image", imageFile);
  form.append("use_ai", String(useAi));

  const res = await fetch("/api/search", {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Search request failed");
  }
  return res.json();
}

export async function fetchSearchPage(searchId, page) {
  const res = await fetch(`/api/search/${encodeURIComponent(searchId)}?page=${page}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to load page");
  }
  return res.json();
}

export async function fetchAdminOverview(adminToken = "") {
  const res = await fetch("/api/admin/overview", {
    headers: adminToken ? { "X-Admin-Token": adminToken } : {},
  });
  if (res.status === 401) {
    const err = new Error("Unauthorized");
    err.status = 401;
    throw err;
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to load admin overview");
  }
  return res.json();
}

export async function triggerAdminSync(syncType, adminToken = "") {
  const res = await fetch("/api/admin/sync", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(adminToken ? { "X-Admin-Token": adminToken } : {}),
    },
    body: JSON.stringify({ sync_type: syncType }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to trigger sync");
  }
  return res.json();
}
