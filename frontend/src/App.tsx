import { useEffect, useMemo, useRef, useState } from "react";
import type { DragEvent, ReactNode } from "react";
import { AnimatePresence, motion } from "framer-motion";
import ProductAccessGate from "../components/ProductAccessGate";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  CloudUpload,
  Database,
  Download,
  FileSpreadsheet,
  FileText,
  Fingerprint,
  Gauge,
  LoaderCircle,
  LockKeyhole,
  Radar,
  RefreshCw,
  ScanSearch,
  ShieldCheck,
  Sparkles,
  Zap,
} from "lucide-react";

type Job = {
  job_id?: string;
  status?: string;
  progress?: number;
  error?: string;
  source_a_records?: number;
  source_b_records?: number;
  source_a_logo_assets?: number;
  source_b_logo_assets?: number;
  application_matches?: number;
  match_rows?: number;
  exact_image_identity_100?: number;
  very_high_visual_review?: number;
  high_visual_review?: number;
  files?: string[];
};

type AssetItem = {
  asset_id?: string;
  image_file?: string;
  asset_url?: string | null;
  source_reference?: string;
  application_no?: string;
};

const API_URL = (import.meta.env.VITE_API_URL || "http://127.0.0.1:8020").replace(/\/$/, "");

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

const authHeaders = () => {
  const headers = new Headers();
  const token = localStorage.getItem("apexive_token") || sessionStorage.getItem("apexive_token");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return headers;
};

function formatBytes(value: number) {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function App() {
  const [sourceA, setSourceA] = useState<File | null>(null);
  const [sourceB, setSourceB] = useState<File | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState<"source-a" | "source-b" | null>(null);
  const [pulse, setPulse] = useState(0);
  const pollRef = useRef<number | null>(null);

  useEffect(() => () => {
    if (pollRef.current) window.clearInterval(pollRef.current);
  }, []);

  const progress = Math.max(0, Math.min(100, Number(job?.progress || 0)));
  const completed = job?.status === "completed";
  const failed = job?.status === "error";

  const stats = useMemo(() => [
    { label: "Source A records", value: job?.source_a_records ?? "—", icon: FileText },
    { label: "Source B records", value: job?.source_b_records ?? "—", icon: FileSpreadsheet },
    { label: "Logo assets", value: job ? (job.source_a_logo_assets ?? 0) + (job.source_b_logo_assets ?? 0) : "—", icon: ScanSearch },
    { label: "Evidence rows", value: job?.match_rows ?? "—", icon: ShieldCheck },
  ], [job]);

  // Assets viewer state
  const [assets, setAssets] = useState<AssetItem[]>([]);
  const [assetsSide, setAssetsSide] = useState<"A" | "B" | null>(null);
  const [assetsLoading, setAssetsLoading] = useState(false);
  const [assetsVisible, setAssetsVisible] = useState(false);
  const [visibleAssetCount, setVisibleAssetCount] = useState(120);

  const onCloseAssets = () => {
    setAssetsVisible(false);
    setAssets([]);
    setAssetsSide(null);
    setVisibleAssetCount(120);
  };

  useEffect(() => {
    setVisibleAssetCount(120);
  }, [assetsVisible, assetsSide, assets.length]);

  async function loadAllAssets(side: "A" | "B") {
    if (!job?.job_id) return;
    setAssets([]);
    setAssetsSide(side);
    setAssetsLoading(true);
    setAssetsVisible(true);
    try {
      const limit = 500;
      let offset = 0;
      let total = 0;
      const collected: AssetItem[] = [];
      do {
        const r = await fetch(`${API_URL}/api/jobs/${job.job_id}/assets/${side}?offset=${offset}&limit=${limit}`, {
          headers: authHeaders(),
          cache: "no-store",
        });
        if (!r.ok) throw new Error(`Failed to fetch assets (${r.status})`);
        const data = await r.json();
        total = data.total || 0;
        collected.push(...(data.items || []));
        offset += (data.limit || limit);
        await sleep(60);
      } while (collected.length < total);
      setAssets(collected);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setAssets([]);
      setAssetsVisible(false);
    } finally {
      setAssetsLoading(false);
    }
  }

  async function exportAssetZip(side: "A" | "B") {
    if (!job?.job_id) return;
    try {
      const response = await fetch(`${API_URL}/api/jobs/${job.job_id}/assets/${side}/zip`, {
        headers: authHeaders(),
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`ZIP export failed (${response.status})`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `source_${side.toLowerCase()}_assets.zip`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  function acceptFile(file: File, source: "source-a" | "source-b") {
    if (!/\.(pdf|xlsx|xlsm)$/i.test(file.name)) {
      setError("Choose a PDF, XLSX, or XLSM file.");
      return;
    }
    if (source === "source-a") setSourceA(file);
    else setSourceB(file);
    setError("");
    setPulse((x) => x + 1);
  }

  function onDrop(event: DragEvent, source: "source-a" | "source-b") {
    event.preventDefault();
    setDragging(null);
    const file = event.dataTransfer.files?.[0];
    if (file) acceptFile(file, source);
  }

  async function startAnalysis() {
    if (!sourceA || !sourceB || busy) return;
    setBusy(true);
    setError("");
    setJob({ status: "uploading", progress: 0 });

    try {
      const body = new FormData();
      body.append("source_a", sourceA);
      body.append("source_b", sourceB);

      const response = await fetch(`${API_URL}/api/analyze`, { method: "POST", body });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `Upload failed (${response.status})`);

      const id = String(data.job_id);
      setJob({ job_id: id, status: "queued", progress: 0 });

      if (pollRef.current) window.clearInterval(pollRef.current);
      pollRef.current = window.setInterval(async () => {
        try {
          const r = await fetch(`${API_URL}/api/jobs/${id}`);
          const state = await r.json();
          if (!r.ok) throw new Error(state.detail || "Job status failed");
          setJob(state);
          if (state.status === "completed" || state.status === "error") {
            if (pollRef.current) window.clearInterval(pollRef.current);
            pollRef.current = null;
            setBusy(false);
          }
        } catch (pollError) {
          if (pollRef.current) window.clearInterval(pollRef.current);
          pollRef.current = null;
          setBusy(false);
          setError(pollError instanceof Error ? pollError.message : "Unable to read job status.");
        }
      }, 900);

      await sleep(250);
    } catch (requestError) {
      setBusy(false);
      setJob(null);
      setError(requestError instanceof Error ? requestError.message : "Analysis could not start.");
    }
  }

  function reset() {
    if (pollRef.current) window.clearInterval(pollRef.current);
    pollRef.current = null;
    setBusy(false);
    setJob(null);
    setError("");
  }

  const fileUrl = (name: string) => `${API_URL}/api/jobs/${job?.job_id}/files/${encodeURIComponent(name)}`;

  return (
    <ProductAccessGate
      productKey="trademark"
      productName="Trademark Conflict Detector"
    >
      <main className="app-shell">
        <div className="aurora aurora-a" />
        <div className="aurora aurora-b" />
        <div className="grid-noise" />

        <header className="topbar">
          <div className="brand-lockup">
            <motion.div
              className="brand-orbit"
              animate={{ rotate: 360 }}
              transition={{ duration: 18, repeat: Infinity, ease: "linear" }}
            >
              <span />
            </motion.div>
            <div>
              <div className="brand-name">APEXIVE AI</div>
              <div className="brand-sub">LEGAL INTELLIGENCE SYSTEM</div>
            </div>
          </div>
          <div className="top-status"><CircleDot size={12} /> ENGINE ONLINE <span>v3.0</span></div>
        </header>

        <section className="hero">
          <motion.div
            className="eyebrow"
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
          >
            <Sparkles size={14} /> VISUAL EVIDENCE • DOCUMENT INTELLIGENCE
          </motion.div>
          <motion.h1
            initial={{ opacity: 0, y: 25, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ duration: 0.65 }}
          >
            Trademark <span>Conflict</span><br />Detector
          </motion.h1>
          <p>
            Compare trademark records across PDFs and workbooks with deterministic image evidence,
            application-number checks, structural verification and report-ready coordinates.
          </p>

          <div className="hero-pills">
            {[
              [Fingerprint, "SHA / pHash / dHash"],
              [Radar, "SSIM / contour / SIFT"],
              [Database, "6,000 × 6,000 ready"],
            ].map(([Icon, text], index) => {
              const I = Icon as typeof Fingerprint;
              const label = text as string;

              return (
                <motion.div
                  key={label}
                  className="hero-pill"
                  animate={{
                    y: [0, -4, 0],
                  }}
                  transition={{
                    duration: 2.2,
                    delay: index * 0.2,
                    repeat: Infinity,
                  }}
                >
                  <I size={15} />
                  {label}
                </motion.div>
              );
            })}
          </div>
        </section>

        <section className="workspace">
          <div className="compare-line" aria-hidden="true">
            <motion.div className="beam beam-left" animate={{ opacity: [0.25, 1, 0.25] }} transition={{ duration: 1.6, repeat: Infinity }} />
            <motion.div className="core-bounce" animate={{ y: [0, -16, 0], scale: [1, 1.08, 1] }} transition={{ duration: 1.55, repeat: Infinity, ease: "easeInOut" }}>
              <Zap size={21} />
            </motion.div>
            <motion.div className="beam beam-right" animate={{ opacity: [1, 0.25, 1] }} transition={{ duration: 1.6, repeat: Infinity }} />
          </div>

          <UploadCard
            title="Source A"
            subtitle="PDF, XLSX, or XLSM trademark records"
            file={sourceA}
            icon={<FileText />}
            dragging={dragging === "source-a"}
            onDrag={(value) => setDragging(value ? "source-a" : null)}
            onDrop={(event) => onDrop(event, "source-a")}
            onFile={(file) => acceptFile(file, "source-a")}
            accent="violet"
          />

          <div className="versus">VS</div>

          <UploadCard
            title="Source B"
            subtitle="PDF, XLSX, or XLSM trademark records"
            file={sourceB}
            icon={<FileSpreadsheet />}
            dragging={dragging === "source-b"}
            onDrag={(value) => setDragging(value ? "source-b" : null)}
            onDrop={(event) => onDrop(event, "source-b")}
            onFile={(file) => acceptFile(file, "source-b")}
            accent="cyan"
          />
        </section>

        <section className="control-panel">
          <div className="control-copy">
            <div className="control-icon"><LockKeyhole size={18} /></div>
            <div>
              <strong>Evidence-first analysis</strong>
              <span>Application number equality is tracked separately from visual identity.</span>
            </div>
          </div>
          <motion.button
            className="analyze-button"
            disabled={!sourceA || !sourceB || busy}
            onClick={startAnalysis}
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.97 }}
          >
            {busy ? <LoaderCircle className="spin" size={19} /> : <ScanSearch size={19} />}
            {busy ? "ANALYZING…" : "RUN PREMIUM ANALYSIS"}
            {!busy && <ArrowRight size={17} />}
          </motion.button>
        </section>

        <AnimatePresence>
          {error && (
            <motion.div className="alert error" initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
              <AlertTriangle size={18} /> {error}
            </motion.div>
          )}
        </AnimatePresence>

        <AnimatePresence mode="wait">
          {job && (
            <motion.section
              className="results-card"
              key={job.job_id || "job"}
              initial={{ opacity: 0, y: 25 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.45 }}
            >
              <div className="results-head">
                <div>
                  <div className="section-kicker"><Gauge size={14} /> LIVE ANALYSIS TELEMETRY</div>
                  <h2>{completed ? "Evidence package ready" : failed ? "Analysis stopped" : "Engine is processing"}</h2>
                </div>
                <div className={`state-badge ${completed ? "ok" : failed ? "bad" : "live"}`}>
                  {completed ? <CheckCircle2 size={14} /> : failed ? <AlertTriangle size={14} /> : <LoaderCircle className="spin" size={14} />}
                  {String(job.status || "queued").toUpperCase()}
                </div>
              </div>

              <div className="progress-wrap">
                <div className="progress-label"><span>ENGINE PROGRESS</span><b>{progress}%</b></div>
                <div className="progress-track"><motion.div className="progress-bar" animate={{ width: `${progress}%` }} transition={{ ease: "easeOut" }} /></div>
              </div>

              <div className="stats-grid">
                {stats.map((stat, i) => {
                  const I = stat.icon;
                  return <motion.div className="stat" key={stat.label} animate={{ y: [0, i % 2 ? -3 : 0, 0] }} transition={{ duration: 2.4, repeat: Infinity, delay: i * 0.15 }}><I size={17} /><span>{stat.label}</span><strong>{stat.value}</strong></motion.div>;
                })}
              </div>

              {completed && (
                <>
                  <div className="download-grid">
                    {(job.files || []).map((name) => (
                      <a className="download-card" key={name} href={fileUrl(name)} target="_blank" rel="noreferrer">
                        <Download size={17} />
                        <span>{name}</span>
                        <ChevronRight size={16} />
                      </a>
                    ))}
                  </div>

                  <div className="assets-controls" style={{display: 'flex', gap: 12, marginTop: 12}}>
                    <button
                      className="download-card"
                      onClick={() => loadAllAssets('A')}
                      disabled={assetsLoading && assetsSide === 'A'}
                      title="Load all Source A assets (aggregates paginated API calls)"
                    >
                      <ScanSearch size={17} />
                      <span>View Source A assets</span>
                    </button>

                    <button
                      className="download-card"
                      onClick={() => loadAllAssets('B')}
                      disabled={assetsLoading && assetsSide === 'B'}
                      title="Load all Source B assets (aggregates paginated API calls)"
                    >
                      <ScanSearch size={17} />
                      <span>View Source B assets</span>
                    </button>

                    <div style={{alignSelf: 'center', color: '#8a98a6'}}>
                      {assetsLoading ? `Loading ${assetsSide} assets…` : assets.length ? `${assets.length} assets loaded` : ''}
                    </div>
                  </div>
                </>
              )}

              {failed && <div className="alert error inline"><AlertTriangle size={17} /> {job.error || "Unknown engine error."}</div>}

              {(completed || failed) && <button className="reset-button" onClick={reset}><RefreshCw size={15} /> NEW ANALYSIS</button>}

              {/* Assets modal / grid */}
              {assetsVisible && (
                <div className="assets-modal" style={{position:'fixed',inset:0,background:'rgba(0,0,0,0.6)',display:'flex',justifyContent:'center',alignItems:'center',zIndex:1200}}>
                  <div style={{width:'90%',height:'90%',background:'#07121a',borderRadius:12,overflow:'hidden',padding:16,display:'flex',flexDirection:'column'}}>
                    <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:12,flexWrap:'wrap',gap:12}}>
                      <h3 style={{margin:0}}>Assets — Source {assetsSide}</h3>
                      <div style={{display:'flex',gap:8,flexWrap:'wrap'}}>
                        <button className="download-card" onClick={() => exportAssetZip(assetsSide || 'A')}>
                          <Download size={17} />
                          <span>ZIP export</span>
                        </button>
                        <a className="download-card" href={fileUrl(`matches.csv`)} target="_blank" rel="noreferrer" style={{alignSelf:'center'}}><Download size={17} /><span>Download CSV</span></a>
                        <button className="reset-button" onClick={() => { setAssetsVisible(false); setAssets([]); setAssetsSide(null); setVisibleAssetCount(120); }}><RefreshCw size={15} /> CLOSE</button>
                      </div>
                    </div>

                    <div
                      onScroll={(event) => {
                        const el = event.currentTarget;
                        const reachedBottom = el.scrollHeight - (el.scrollTop + el.clientHeight) < 220;
                        if (reachedBottom && visibleAssetCount < assets.length) {
                          setVisibleAssetCount((count) => Math.min(count + 120, assets.length));
                        }
                      }}
                      style={{overflowY:'auto',paddingRight:6,display:'grid'}}
                    >
                      <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill, minmax(140px, 1fr))',gap:12}}>
                        {assets.slice(0, visibleAssetCount).map((item, i) => {
                          const assetUrl = item.asset_url ? `${API_URL}${item.asset_url}` : "";
                          return (
                            <div key={item.asset_id || i} style={{background:'#071821',borderRadius:8,padding:8,display:'flex',flexDirection:'column',gap:8,minHeight:180}}>
                              {item.image_file && assetUrl ? (
                                <img src={assetUrl} alt={item.asset_id} loading="lazy" style={{width:'100%',height:120,objectFit:'contain',background:'#fff'}} />
                              ) : (
                                <div style={{height:120,display:'flex',alignItems:'center',justifyContent:'center',color:'#8a98a6'}}>No image</div>
                              )}
                              <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',gap:8}}>
                                <small style={{color:'#8a98a6',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{item.asset_id || `Asset ${i + 1}`}</small>
                                {item.image_file && assetUrl && (
                                  <a href={assetUrl} target="_blank" rel="noreferrer" style={{color:'#58e8ff',whiteSpace:'nowrap'}}>Open</a>
                                )}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>

                    {visibleAssetCount < assets.length && (
                      <div style={{display:'flex',justifyContent:'center',paddingTop:12}}>
                        <button className="reset-button" onClick={() => setVisibleAssetCount((count) => Math.min(count + 120, assets.length))}>LOAD MORE</button>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </motion.section>
          )}
        </AnimatePresence>

        <section className="spec-grid">
          <Spec icon={<ShieldCheck />} title="Deterministic evidence" text="SHA-256, normalized visual hashes, mask overlap, SSIM, contour and SIFT agreement." />
          <Spec icon={<FileText />} title="PDF → PDF" text="Government journal records remain the authoritative source for page coordinates." />
          <Spec icon={<FileSpreadsheet />} title="XLSX → XLSX" text="Workbook rows and embedded media are mapped back to application records." />
          <Spec icon={<ArrowRight />} title="PDF ↔ XLSX" text="Cross-format evidence pairs are generated with marked PDF and XLSX reports." />
        </section>

        <footer>
          <span>APEXIVE AI • TRADEMARK CONFLICT DETECTOR</span>
          <span>Technical evidence only • Human / legal review required</span>
        </footer>
      </main>
    </ProductAccessGate>
  );
}

function UploadCard(props: {
  title: string;
  subtitle: string;
  file: File | null;
  icon: ReactNode;
  dragging: boolean;
  accent: "violet" | "cyan";
  onDrag: (value: boolean) => void;
  onDrop: (event: React.DragEvent) => void;
  onFile: (file: File) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <motion.div
      className={`upload-card ${props.accent} ${props.dragging ? "dragging" : ""} ${props.file ? "selected" : ""}`}
      onDragOver={(event: DragEvent) => { event.preventDefault(); props.onDrag(true); }}
      onDragLeave={() => props.onDrag(false)}
      onDrop={props.onDrop}
      onClick={() => inputRef.current?.click()}
      whileHover={{ y: -7 }}
      animate={props.file ? { boxShadow: ["0 0 0 rgba(0,0,0,0)", "0 0 42px rgba(135,95,255,.18)", "0 0 0 rgba(0,0,0,0)"] } : undefined}
      transition={{ duration: 2.4, repeat: props.file ? Infinity : 0 }}
    >
      <input ref={inputRef} hidden type="file" accept=".pdf,application/pdf,.xlsx,.xlsm" onChange={(e) => { const file = e.target.files?.[0]; if (file) props.onFile(file); e.target.value = ""; }} />
      <div className="upload-icon">{props.file ? <CheckCircle2 /> : props.icon}</div>
      <div className="upload-meta">
        <div className="upload-title">{props.title}</div>
        <div className="upload-sub">{props.subtitle}</div>
        <div className="upload-file">{props.file ? <><span>{props.file.name}</span><small>{formatBytes(props.file.size)}</small></> : <><CloudUpload size={15} /> DROP FILE OR CLICK TO BROWSE</>}</div>
      </div>
      <div className="corner corner-a" /><div className="corner corner-b" />
    </motion.div>
  );
}

function Spec({ icon, title, text }: { icon: ReactNode; title: string; text: string }) {
  return <motion.article className="spec" whileHover={{ y: -4 }}><div className="spec-icon">{icon}</div><div><strong>{title}</strong><p>{text}</p></div></motion.article>;
}

export default App;
