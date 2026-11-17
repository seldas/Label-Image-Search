import React, { useEffect, useRef, useState } from "react";
import {
  Search,
  ImagePlus,
  X,
  Sparkles,
  ChevronLeft,
  ChevronRight,
  Database,
  Loader2,
  Info,
  Settings,
} from "lucide-react";
import { fetchSearchPage, fetchStatus, searchImages } from "./api";
import AdminDashboard from "./AdminDashboard.jsx";

export default function App() {
  const [activeTab, setActiveTab] = useState("search");
  const [text, setText] = useState("");
  const [imageFile, setImageFile] = useState(null);
  const [imagePreview, setImagePreview] = useState("");
  const [useAi, setUseAi] = useState(true);

  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [result, setResult] = useState(null);
  const [selectedItem, setSelectedItem] = useState(null);

  const fileInputRef = useRef(null);

  useEffect(() => {
    fetchStatus().then(setStatus).catch(() => {});
  }, []);

  useEffect(() => {
    const onPaste = (e) => {
      const items = e.clipboardData?.items;
      if (!items) return;
      for (const item of items) {
        if (item.type.startsWith("image/")) {
          const file = item.getAsFile();
          if (file) handleSelectFile(file);
          break;
        }
      }
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, []);

  const handleSelectFile = (file) => {
    if (!file) return;
    setImageFile(file);
    setImagePreview(URL.createObjectURL(file));
  };

  const clearImage = () => {
    if (imagePreview) URL.revokeObjectURL(imagePreview);
    setImageFile(null);
    setImagePreview("");
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handleSearch = async (e) => {
    if (e) e.preventDefault();
    if (!text.trim() && !imageFile) {
      setError("Please enter a description or upload a reference image.");
      return;
    }
    setError("");
    setLoading(true);
    try {
      const data = await searchImages({ text, imageFile, useAi });
      setResult(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handlePageChange = async (nextPage) => {
    if (!result || nextPage < 1 || nextPage > result.total_pages) return;
    setLoading(true);
    setError("");
    try {
      const data = await fetchSearchPage(result.search_id, nextPage);
      setResult(data);
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="bg-white border-b border-slate-200 sticky top-0 z-20">
        <div className="max-w-6xl mx-auto px-4 py-3 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-blue-600 text-white flex items-center justify-center font-bold">
              LQ
            </div>
            <div>
              <h1 className="text-base font-semibold text-slate-900">
                Labeling Image Query
              </h1>
              <p className="text-xs text-slate-500">
                DailyMed SPL Local Offline Search · 3×3 Grid
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            {status && (
              <div className="hidden md:flex items-center gap-2 text-xs bg-slate-100 text-slate-700 px-3 py-1.5 rounded-full">
                <Database className="w-3.5 h-3.5 text-blue-600" />
                <span>
                  Local DB: <strong>{status.total_images?.toLocaleString()}</strong> images /{" "}
                  <strong>{status.total_spl_documents?.toLocaleString()}</strong> SPL docs
                </span>
              </div>
            )}

            <nav className="flex items-center bg-slate-100 p-1 rounded-xl text-xs font-medium">
              <button
                onClick={() => setActiveTab("search")}
                className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg transition ${
                  activeTab === "search"
                    ? "bg-white text-slate-900 shadow-sm"
                    : "text-slate-600 hover:text-slate-900"
                }`}
              >
                <Search className="w-3.5 h-3.5" />
                Image Search
              </button>
              <button
                onClick={() => setActiveTab("admin")}
                className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg transition ${
                  activeTab === "admin"
                    ? "bg-white text-slate-900 shadow-sm"
                    : "text-slate-600 hover:text-slate-900"
                }`}
              >
                <Settings className="w-3.5 h-3.5" />
                Admin
              </button>
            </nav>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1 max-w-6xl w-full mx-auto px-4 py-6">
        {activeTab === "admin" ? (
          <AdminDashboard />
        ) : (
          <>
        {/* Search Form */}
        <form
          onSubmit={handleSearch}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            const file = e.dataTransfer.files?.[0];
            if (file && file.type.startsWith("image/")) handleSelectFile(file);
          }}
          className="bg-white rounded-2xl shadow-sm border border-slate-200 p-4 mb-6"
        >
          <div className="flex flex-col sm:flex-row gap-3">
            {imagePreview && (
              <div className="relative w-20 h-20 rounded-xl overflow-hidden border border-slate-200 shrink-0 bg-slate-100">
                <img
                  src={imagePreview}
                  alt="Query preview"
                  className="w-full h-full object-contain"
                />
                <button
                  type="button"
                  onClick={clearImage}
                  className="absolute top-1 right-1 bg-slate-900/70 text-white rounded-full p-0.5 hover:bg-slate-900"
                  title="Remove image"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            )}

            <div className="flex-1 flex flex-col justify-between gap-2">
              <input
                type="text"
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="Enter drug name or visual description (e.g., white oval scored tablet, blue box), or paste (Ctrl+V) / drop an image..."
                className="w-full px-3.5 py-2.5 text-sm rounded-xl border border-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />

              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-3">
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/*"
                    className="hidden"
                    onChange={(e) => handleSelectFile(e.target.files?.[0])}
                  />
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-700 hover:text-blue-600 bg-slate-100 hover:bg-blue-50 px-3 py-1.5 rounded-lg transition"
                  >
                    <ImagePlus className="w-4 h-4" />
                    Upload Image
                  </button>

                  <label className="inline-flex items-center gap-1.5 text-xs text-slate-600 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={useAi}
                      onChange={(e) => setUseAi(e.target.checked)}
                      className="rounded text-blue-600 focus:ring-blue-500"
                    />
                    <Sparkles className="w-3.5 h-3.5 text-amber-500" />
                    Enable AI Query Understanding (Gemma)
                  </label>
                </div>

                <button
                  type="submit"
                  disabled={loading}
                  className="inline-flex items-center gap-1.5 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-400 text-white text-sm font-medium px-5 py-2 rounded-xl transition"
                >
                  {loading ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Search className="w-4 h-4" />
                  )}
                  Search
                </button>
              </div>
            </div>
          </div>
        </form>

        {error && (
          <div className="mb-4 p-3 rounded-xl bg-red-50 border border-red-200 text-red-700 text-sm">
            {error}
          </div>
        )}

        {/* AI Query Rewrite Info */}
        {result && (result.ai_reasoning || result.rewritten_query) && (
          <div className="mb-4 px-4 py-2.5 rounded-xl bg-blue-50/70 border border-blue-100 text-xs text-slate-700 flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Info className="w-4 h-4 text-blue-600 shrink-0" />
              <span>
                {result.ai_reasoning && <span>{result.ai_reasoning} · </span>}
                {result.rewritten_query && (
                  <span className="text-slate-500">
                    Query: <code className="bg-white px-1.5 py-0.5 rounded">{result.rewritten_query}</code>
                  </span>
                )}
              </span>
            </div>
            <span className="text-slate-500">
              {result.total} matches · Page {result.page} of {result.total_pages}
            </span>
          </div>
        )}

        {/* 3x3 Nine-Grid Results */}
        {result && result.items.length > 0 ? (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
              {result.items.map((item) => (
                <div
                  key={item.image_id}
                  onClick={() => setSelectedItem(item)}
                  className="group bg-white rounded-2xl border border-slate-200 overflow-hidden shadow-sm hover:shadow-md hover:border-blue-300 transition cursor-pointer flex flex-col"
                >
                  <div className="aspect-square bg-slate-100 flex items-center justify-center p-3 overflow-hidden">
                    <img
                      src={item.thumb_url}
                      alt={item.product_name || item.image_id}
                      loading="lazy"
                      className="max-w-full max-h-full object-contain group-hover:scale-105 transition-transform duration-200"
                    />
                  </div>
                  <div className="p-3 border-t border-slate-100 flex-1 flex flex-col justify-between">
                    <div>
                      <h3 className="text-sm font-medium text-slate-900 line-clamp-1">
                        {item.product_name || "Unknown Product"}
                      </h3>
                      <p className="text-xs text-slate-500 line-clamp-1 mt-0.5">
                        {item.generic_name || item.section_title || item.setid}
                      </p>
                    </div>
                    <div className="mt-2 flex items-center justify-between text-[11px] text-slate-400">
                      <span className="truncate max-w-[65%]">{item.manufacturer}</span>
                      <span className="bg-slate-100 text-slate-600 px-1.5 py-0.5 rounded">
                        {item.match_sources.join(" + ")}
                      </span>
                    </div>
                  </div>
                </div>
              ))}
            </div>

            {/* Pagination Controls */}
            {result.total_pages > 1 && (
              <div className="mt-8 flex items-center justify-center gap-2">
                <button
                  onClick={() => handlePageChange(result.page - 1)}
                  disabled={result.page <= 1 || loading}
                  className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-40"
                >
                  <ChevronLeft className="w-4 h-4" />
                  Prev
                </button>

                <div className="flex items-center gap-1">
                  {Array.from({ length: result.total_pages }, (_, idx) => idx + 1).map((p) => (
                    <button
                      key={p}
                      onClick={() => handlePageChange(p)}
                      disabled={loading}
                      className={`w-8 h-8 rounded-lg text-sm font-medium transition ${
                        p === result.page
                          ? "bg-blue-600 text-white"
                          : "bg-white border border-slate-200 text-slate-700 hover:bg-slate-50"
                      }`}
                    >
                      {p}
                    </button>
                  ))}
                </div>

                <button
                  onClick={() => handlePageChange(result.page + 1)}
                  disabled={result.page >= result.total_pages || loading}
                  className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-40"
                >
                  Next
                  <ChevronRight className="w-4 h-4" />
                </button>
              </div>
            )}
          </>
        ) : result ? (
          <div className="text-center py-16 text-slate-500 text-sm bg-white rounded-2xl border border-slate-200">
            No matching labeling images found. Try refining your description or reference image.
          </div>
        ) : (
          <div className="text-center py-20 text-slate-400 text-sm">
            Enter a text description or upload an image to search (3×3 grid per page)
          </div>
        )}
          </>
        )}
      </main>

      {/* Detail Modal */}
      {selectedItem && (
        <div
          className="fixed inset-0 z-50 bg-slate-900/60 flex items-center justify-center p-4"
          onClick={() => setSelectedItem(null)}
        >
          <div
            className="bg-white rounded-2xl max-w-4xl w-full max-h-[90vh] overflow-hidden shadow-xl flex flex-col md:flex-row"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="md:w-1/2 bg-slate-100 flex items-center justify-center p-6 min-h-[280px]">
              <img
                src={selectedItem.orig_url}
                alt={selectedItem.product_name}
                className="max-w-full max-h-[70vh] object-contain"
              />
            </div>
            <div className="md:w-1/2 p-6 overflow-y-auto flex flex-col justify-between gap-4">
              <div>
                <div className="flex items-start justify-between gap-2">
                  <h2 className="text-lg font-semibold text-slate-900">
                    {selectedItem.product_name || "Labeling Image"}
                  </h2>
                  <button
                    onClick={() => setSelectedItem(null)}
                    className="p-1 rounded-lg hover:bg-slate-100 text-slate-500"
                  >
                    <X className="w-5 h-5" />
                  </button>
                </div>
                {selectedItem.generic_name && (
                  <p className="text-sm text-slate-600 mt-1">{selectedItem.generic_name}</p>
                )}

                <dl className="mt-4 space-y-2 text-xs">
                  <div className="flex gap-2">
                    <dt className="text-slate-400 w-24 shrink-0">SetID:</dt>
                    <dd className="text-slate-700 font-mono break-all">{selectedItem.setid}</dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="text-slate-400 w-24 shrink-0">Manufacturer:</dt>
                    <dd className="text-slate-700">{selectedItem.manufacturer || "-"}</dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="text-slate-400 w-24 shrink-0">Category / Size:</dt>
                    <dd className="text-slate-700">
                      {selectedItem.category || "-"} · {selectedItem.width}×{selectedItem.height}
                    </dd>
                  </div>
                  {selectedItem.section_title && (
                    <div className="flex gap-2">
                      <dt className="text-slate-400 w-24 shrink-0">SPL Section:</dt>
                      <dd className="text-slate-700">{selectedItem.section_title}</dd>
                    </div>
                  )}
                </dl>

                {selectedItem.xml_context && (
                  <div className="mt-4">
                    <h4 className="text-xs font-medium text-slate-500 mb-1">SPL Context:</h4>
                    <p className="text-xs text-slate-600 bg-slate-50 p-3 rounded-xl border border-slate-100 leading-relaxed max-h-40 overflow-y-auto">
                      {selectedItem.xml_context}
                    </p>
                  </div>
                )}

                {selectedItem.vlm_caption && (
                  <div className="mt-3">
                    <h4 className="text-xs font-medium text-slate-500 mb-1">AI Visual Caption:</h4>
                    <p className="text-xs text-slate-600 bg-blue-50/50 p-3 rounded-xl border border-blue-100">
                      {selectedItem.vlm_caption}
                    </p>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
