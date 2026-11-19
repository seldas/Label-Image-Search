import React, { useEffect, useState } from "react";
import {
  RefreshCw,
  Play,
  Database,
  HardDrive,
  Clock,
  CheckCircle2,
  AlertCircle,
  Loader2,
  Lock,
  Cpu,
  X,
  Copy,
} from "lucide-react";
import { fetchAdminOverview, triggerAdminSync } from "./api";

export default function AdminDashboard() {
  const [token, setToken] = useState(() => sessionStorage.getItem("admin_token") || "");
  const [tokenInput, setTokenInput] = useState("");
  const [needsAuth, setNeedsAuth] = useState(false);

  const [overview, setOverview] = useState(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selectedErrorLog, setSelectedErrorLog] = useState(null);
  const [copied, setCopied] = useState(false);

  const loadOverview = async (activeToken = token) => {
    setError("");
    try {
      const data = await fetchAdminOverview(activeToken);
      setOverview(data);
      setNeedsAuth(false);
    } catch (err) {
      if (err.status === 401) {
        setNeedsAuth(true);
      } else {
        setError(err.message);
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    let timerId = null;

    const poll = async () => {
      await loadOverview();
      if (!cancelled) {
        timerId = setTimeout(poll, 15000);
      }
    };

    poll();
    return () => {
      cancelled = true;
      if (timerId) clearTimeout(timerId);
    };
  }, [token]);

  const handleAuthSubmit = (e) => {
    e.preventDefault();
    sessionStorage.setItem("admin_token", tokenInput);
    setToken(tokenInput);
    setLoading(true);
    loadOverview(tokenInput);
  };

  const handleTriggerSync = async (syncType) => {
    if (
      syncType === "full" &&
      !window.confirm("Full sync downloads the entire DailyMed release and rebuilds indexes, which may take hours. Continue?")
    ) {
      return;
    }
    setSyncing(true);
    setError("");
    setNotice("");
    try {
      const res = await triggerAdminSync(syncType, token);
      setNotice(`Queued ${syncType === "full" ? "Full" : "Weekly"} sync job (Request #${res.request_id}). Worker will pick it up within seconds.`);
      await loadOverview();
    } catch (err) {
      setError(err.message);
    } finally {
      setSyncing(false);
    }
  };

  if (needsAuth) {
    return (
      <div className="max-w-md mx-auto mt-12 bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
        <div className="flex items-center gap-2 text-slate-900 font-semibold mb-2">
          <Lock className="w-5 h-5 text-blue-600" />
          <h2>Admin Authentication</h2>
        </div>
        <p className="text-xs text-slate-500 mb-4">
          Backend <code className="bg-slate-100 px-1 rounded">ADMIN_TOKEN</code> is enabled. Enter your admin token to continue.
        </p>
        <form onSubmit={handleAuthSubmit} className="flex gap-2">
          <input
            type="password"
            value={tokenInput}
            onChange={(e) => setTokenInput(e.target.value)}
            placeholder="Enter Admin Token..."
            className="flex-1 px-3 py-2 text-sm rounded-xl border border-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button
            type="submit"
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-xl"
          >
            Unlock
          </button>
        </form>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Top Action Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">Data & Sync Management</h2>
          <p className="text-xs text-slate-500">
            Manage DailyMed SPL full/weekly sync jobs, local storage, and intranet AI service status
          </p>
        </div>
        <button
          onClick={() => {
            setLoading(true);
            loadOverview();
          }}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-white border border-slate-200 hover:bg-slate-50 text-slate-700 rounded-xl"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {error && (
        <div className="p-3 rounded-xl bg-red-50 border border-red-200 text-red-700 text-sm flex items-center gap-2">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {notice && (
        <div className="p-3 rounded-xl bg-emerald-50 border border-emerald-200 text-emerald-700 text-sm flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 shrink-0" />
          <span>{notice}</span>
        </div>
      )}

      {/* Overview Stats Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-white rounded-2xl border border-slate-200 p-4">
          <div className="flex items-center justify-between text-xs text-slate-500 mb-1">
            <span>Labeling Images</span>
            <Database className="w-4 h-4 text-blue-600" />
          </div>
          <div className="text-2xl font-bold text-slate-900">
            {overview ? overview.stats.total_images.toLocaleString() : "-"}
          </div>
          <div className="text-[11px] text-slate-400 mt-1">
            Linked to {overview ? overview.stats.total_spl_documents.toLocaleString() : "-"} SPL docs
          </div>
        </div>

        <div className="bg-white rounded-2xl border border-slate-200 p-4">
          <div className="flex items-center justify-between text-xs text-slate-500 mb-1">
            <span>Disk &amp; DB Storage</span>
            <HardDrive className="w-4 h-4 text-indigo-600" />
          </div>
          <div className="text-2xl font-bold text-slate-900">
            {overview ? `${overview.storage.disk_free_gb} GB free` : "-"}
          </div>
          <div className="text-[11px] text-slate-400 mt-1">
            DB {overview ? overview.storage.db_mb : 0} MB · Used{" "}
            {overview ? `${overview.storage.disk_used_gb}/${overview.storage.disk_total_gb} GB` : "-"}
          </div>
        </div>

        <div className="bg-white rounded-2xl border border-slate-200 p-4">
          <div className="flex items-center justify-between text-xs text-slate-500 mb-1">
            <span>Weekly Scheduler</span>
            <Clock className="w-4 h-4 text-amber-600" />
          </div>
          <div className="text-base font-semibold text-slate-900 mt-1 flex items-center gap-1.5">
            {overview?.active_sync ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin text-blue-600" />
                <span>Running ({overview.active_sync.sync_type})</span>
              </>
            ) : (
              <span>Idle</span>
            )}
          </div>
          <div className="text-[11px] text-slate-400 mt-1">
            Schedule: {overview?.config.cron_schedule || "sun 02:00 UTC"}
          </div>
        </div>

        <div className="bg-white rounded-2xl border border-slate-200 p-4">
          <div className="flex items-center justify-between text-xs text-slate-500 mb-1">
            <span>Intranet AI & Embedding</span>
            <Cpu className="w-4 h-4 text-emerald-600" />
          </div>
          <div className="text-sm font-semibold text-slate-900 mt-1 truncate">
            {overview?.config.vllm_enabled ? overview.config.vllm_model : "vLLM Disabled"}
          </div>
          <div className="text-[11px] text-slate-400 mt-1 truncate" title={overview?.config.embedding_model}>
            Embedder: {overview?.config.embedding_model || "-"}
          </div>
        </div>
      </div>

      {/* Sync Trigger Panel */}
      <div className="bg-white rounded-2xl border border-slate-200 p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">Manual DailyMed Data Sync</h3>
          <p className="text-xs text-slate-500 mt-1">
            Active categories:{" "}
            <span className="font-mono text-slate-700">
              {overview?.config.categories.join(", ") || "human_rx, human_otc"}
            </span>
            . Jobs are streamed by the Worker container into PostgreSQL & pgvector.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2.5 shrink-0">
          <button
            onClick={() => handleTriggerSync("weekly")}
            disabled={syncing || Boolean(overview?.active_sync)}
            className="inline-flex items-center gap-1.5 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white text-xs font-medium rounded-xl transition"
          >
            <Play className="w-3.5 h-3.5" />
            Run Weekly Sync
          </button>
          <button
            onClick={() => handleTriggerSync("full")}
            disabled={syncing || Boolean(overview?.active_sync)}
            className="inline-flex items-center gap-1.5 px-4 py-2 bg-slate-100 hover:bg-slate-200 disabled:opacity-40 text-slate-800 text-xs font-medium rounded-xl transition"
          >
            <Database className="w-3.5 h-3.5" />
            Run Full Sync
          </button>
        </div>
      </div>

      {/* Sync Logs Table */}
      <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden">
        <div className="px-5 py-3.5 border-b border-slate-100 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-900">Recent Sync Logs</h3>
          <span className="text-xs text-slate-400">Auto-refreshes every 15s</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-50 text-slate-500 border-b border-slate-100">
              <tr>
                <th className="px-4 py-2.5 font-medium">Type</th>
                <th className="px-4 py-2.5 font-medium">DailyMed Archive</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
                <th className="px-4 py-2.5 font-medium">SetIDs</th>
                <th className="px-4 py-2.5 font-medium">Images Added</th>
                <th className="px-4 py-2.5 font-medium">Started (UTC)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {overview?.sync_logs?.length ? (
                overview.sync_logs.map((log) => (
                  <tr key={log.id} className="hover:bg-slate-50/60">
                    <td className="px-4 py-2.5 font-medium uppercase text-slate-700">
                      {log.sync_type}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-slate-600">{log.filename}</td>
                    <td className="px-4 py-2.5">
                      {log.status === "failed" ? (
                        <button
                          type="button"
                          onClick={() => {
                            setCopied(false);
                            setSelectedErrorLog(log);
                          }}
                          title="Click to inspect error details"
                          className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-red-50 text-red-700 hover:bg-red-100 border border-red-200 cursor-pointer transition"
                        >
                          <AlertCircle className="w-3 h-3" />
                          failed
                        </button>
                      ) : (
                        <span
                          className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium ${
                            log.status === "completed"
                              ? "bg-emerald-50 text-emerald-700"
                              : "bg-blue-50 text-blue-700"
                          }`}
                        >
                          {log.status}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-slate-700">{log.setids_processed}</td>
                    <td className="px-4 py-2.5 text-slate-700">{log.images_added}</td>
                    <td className="px-4 py-2.5 text-slate-400">
                      {log.started_at?.slice(0, 19).replace("T", " ")}
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={6} className="px-4 py-10 text-center text-slate-400">
                    No sync logs yet. Once the Worker container runs on the target server, initial full and weekly sync records will appear here.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
      {/* Error Log Modal */}
      {selectedErrorLog && (
        <div
          className="fixed inset-0 z-50 bg-slate-900/60 flex items-center justify-center p-4"
          onClick={() => setSelectedErrorLog(null)}
        >
          <div
            className="bg-white rounded-2xl max-w-3xl w-full max-h-[85vh] overflow-hidden shadow-xl flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <AlertCircle className="w-5 h-5 text-red-600 shrink-0" />
                <div>
                  <h3 className="text-sm font-semibold text-slate-900">
                    Sync Error Details · {selectedErrorLog.filename}
                  </h3>
                  <p className="text-[11px] text-slate-500">
                    Type: {selectedErrorLog.sync_type} · SetIDs: {selectedErrorLog.setids_processed} ·
                    Images Added: {selectedErrorLog.images_added} · Started:{" "}
                    {selectedErrorLog.started_at?.slice(0, 19).replace("T", " ")} UTC
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                {selectedErrorLog.error_message && (
                  <button
                    type="button"
                    onClick={() => {
                      navigator.clipboard?.writeText(selectedErrorLog.error_message);
                      setCopied(true);
                      setTimeout(() => setCopied(false), 2000);
                    }}
                    className="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-lg"
                  >
                    <Copy className="w-3.5 h-3.5" />
                    {copied ? "Copied" : "Copy"}
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setSelectedErrorLog(null)}
                  className="p-1 rounded-lg hover:bg-slate-100 text-slate-500"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>

            <div className="p-5 overflow-y-auto flex-1 bg-slate-950 text-slate-100">
              <pre className="text-xs font-mono whitespace-pre-wrap break-words leading-relaxed">
                {selectedErrorLog.error_message ||
                  "No error message recorded in DB. The worker container may have been restarted or OOM-killed; check `docker compose logs worker`."}
              </pre>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
