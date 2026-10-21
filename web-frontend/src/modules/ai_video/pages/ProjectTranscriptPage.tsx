import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  applyTranscriptBatch,
  applyTranscriptEdit,
  cancelTranscriptEdit,
  exportTimelineDraft,
  fetchAudioChunk,
  listMedia,
  listTranscriptClips,
  previewTranscriptEdit,
  retryTranscriptEdit,
  updateTranscript,
  uploadMedia,
} from "../api/endpoints/media";
import { getProject } from "../api/endpoints/projects";
import type { MediaSource, Project, TranscriptClipCandidate, TranscriptEditProposal } from "../api/types";
import { ProjectHeader } from "../components/ProjectHeader";
import { aiVideoMediaUrl } from "../lib/media-url";
import { LOCAL_ASR_MODELS, type AsrWorkerMessage, type LocalAsrDevice, type LocalAsrModel } from "../lib/local-asr";
import { buildSrt, buildVtt } from "../lib/subtitle-export";
import {
  ASR_CHUNK_SECONDS,
  appendTranscriptChunk,
  decodeFloat32Pcm,
  transcriptFromCheckpoint,
  type TranscriptCheckpoint,
} from "../lib/transcript-checkpoint";
import { findTranscriptPhrase } from "../lib/transcript-corrections";
import {
  DEFAULT_TRANSCRIPT_EDIT_PLAN,
  detectFillerWordIds,
  findTranscriptWordAtTime,
  keepRangesForPlan,
  nextPlayableSourceTime,
  outputDuration,
  removedRangesForPlan,
  sanitizeTranscriptEditPlan,
  sanitizeTranscriptDocument,
  transcriptWordsToCues,
  type TimeRange,
  type TranscriptEditPlan,
} from "../lib/transcript-editor";
import { TRANSCRIPT_EDIT_FORMAT } from "../lib/transcript-edit-protocol";

type Notice = { kind: "ok" | "error"; text: string } | null;

type EditHistory = { past: TranscriptEditPlan[]; present: TranscriptEditPlan; future: TranscriptEditPlan[] };

const WORD_PAGE_SIZE = 600;

function clonePlan(plan: TranscriptEditPlan): TranscriptEditPlan {
  return {
    ...plan,
    removedWordIds: [...plan.removedWordIds],
    ...(plan.sourceRange ? { sourceRange: { ...plan.sourceRange } } : {}),
    ...(plan.captionReplacements
      ? { captionReplacements: plan.captionReplacements.map((item) => ({ ...item, wordIds: [...item.wordIds] })) }
      : {}),
  };
}

function createHistory(plan = DEFAULT_TRANSCRIPT_EDIT_PLAN): EditHistory {
  return { past: [], present: clonePlan(plan), future: [] };
}

function samePlan(a: TranscriptEditPlan, b: TranscriptEditPlan): boolean {
  return (
    JSON.stringify(a.captionReplacements ?? []) === JSON.stringify(b.captionReplacements ?? []) &&
    a.removeSilence === b.removeSilence &&
    a.burnSubtitles === b.burnSubtitles &&
    a.silencePaddingMs === b.silencePaddingMs &&
    a.wordPaddingMs === b.wordPaddingMs &&
    a.sourceRange?.start === b.sourceRange?.start &&
    a.sourceRange?.end === b.sourceRange?.end &&
    a.removedWordIds.length === b.removedWordIds.length &&
    a.removedWordIds.every((id, index) => id === b.removedWordIds[index])
  );
}

function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`
    : `${minutes}:${String(secs).padStart(2, "0")}`;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function downloadText(fileName: string, mime: string, content: string) {
  const url = URL.createObjectURL(new Blob([content], { type: mime }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

type ProjectTranscriptPageProps = {
  projectId: string;
  onNavigate: (path: string) => void;
};

/** 逐字稿：本地 ASR 分块转写 + 词级编辑 + 非破坏剪辑 + SRT/VTT/时间线导出。 */
export function ProjectTranscriptPage({ projectId, onNavigate }: ProjectTranscriptPageProps) {
  const [project, setProject] = useState<Project | null>(null);
  const [sources, setSources] = useState<MediaSource[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [notice, setNotice] = useState<Notice>(null);

  const [busy, setBusy] = useState<"upload" | "decode" | "transcribe" | "preview" | "render" | "export" | null>(null);
  const [model, setModel] = useState<LocalAsrModel>(LOCAL_ASR_MODELS[0].id);
  const [language, setLanguage] = useState("auto");
  const [device, setDevice] = useState<LocalAsrDevice | null>(null);
  const [fallback, setFallback] = useState(false);
  const [phase, setPhase] = useState<"loading" | "transcribing" | null>(null);
  const [chunkState, setChunkState] = useState({ index: 0, total: 0 });
  const [progress, setProgress] = useState(0);
  const [uploadPercent, setUploadPercent] = useState<number | null>(null);

  const [history, setHistory] = useState<EditHistory>(() => createHistory());
  const [proposal, setProposal] = useState<TranscriptEditProposal | null>(null);
  const [previewCuts, setPreviewCuts] = useState(true);
  const [currentTime, setCurrentTime] = useState(0);
  const [wordPage, setWordPage] = useState(0);

  const [clipQuery, setClipQuery] = useState("");
  const [clipTarget, setClipTarget] = useState(30);
  const [clips, setClips] = useState<TranscriptClipCandidate[]>([]);
  const [clipSelected, setClipSelected] = useState<Set<string>>(new Set());
  const [clipsBusy, setClipsBusy] = useState(false);

  const [correctionQuery, setCorrectionQuery] = useState("");
  const [correctionText, setCorrectionText] = useState("");

  const inputRef = useRef<HTMLInputElement | null>(null);
  const planInputRef = useRef<HTMLInputElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const workerRef = useRef<Worker | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const cancelRequestedRef = useRef(false);
  const deviceRef = useRef<LocalAsrDevice | null>(null);
  const heartbeatRef = useRef<number | null>(null);
  const progressRef = useRef(0);
  const clipPreviewRef = useRef<TimeRange | null>(null);

  const loadSources = useCallback(async () => {
    try {
      const { sources: next } = await listMedia(projectId);
      setSources(next ?? []);
      setSelectedId((current) =>
        current && next.some((source) => source.id === current) ? current : next.find((source) => source.status === "ready")?.id ?? next[0]?.id ?? "",
      );
      setLoadError("");
    } catch (cause) {
      setLoadError(cause instanceof Error ? cause.message : "导入素材加载失败");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void loadSources();
    void getProject(projectId).then(setProject).catch(() => undefined);
  }, [loadSources, projectId]);

  useEffect(
    () => () => {
      abortRef.current?.abort();
      workerRef.current?.terminate();
      if (heartbeatRef.current) clearInterval(heartbeatRef.current);
    },
    [],
  );

  const hasRendering = sources.some((source) => source.edits.some((edit) => edit.status === "rendering" || edit.status === "queued"));
  useEffect(() => {
    if (!hasRendering) return;
    const timer = window.setInterval(() => void loadSources(), 2500);
    return () => window.clearInterval(timer);
  }, [hasRendering, loadSources]);

  const selected = sources.find((source) => source.id === selectedId) ?? null;
  const transcript = useMemo(
    () => (selected ? sanitizeTranscriptDocument(selected.transcript, selected.duration / 1000) : null),
    [selected],
  );
  const plan = history.present;
  const removedIdSet = useMemo(() => new Set(plan.removedWordIds), [plan.removedWordIds]);
  const keepRanges = useMemo(() => (transcript ? keepRangesForPlan(transcript, plan) : []), [plan, transcript]);
  const removedRanges = useMemo(() => (transcript ? removedRangesForPlan(transcript, plan) : []), [plan, transcript]);
  const editedSeconds = outputDuration(keepRanges);
  const removedSeconds = removedRanges.reduce((sum, range) => sum + range.end - range.start, 0);
  const fillerIds = useMemo(() => (transcript ? detectFillerWordIds(transcript) : []), [transcript]);
  const remainingFillerIds = useMemo(() => fillerIds.filter((id) => !removedIdSet.has(id)), [fillerIds, removedIdSet]);
  const activeWordId = transcript ? findTranscriptWordAtTime(transcript.words, currentTime)?.id ?? null : null;
  const latestRevision = selected?.edits.reduce((max, edit) => Math.max(max, edit.revision), 0) ?? 0;
  const words = transcript?.words ?? [];
  const pageCount = Math.max(1, Math.ceil(words.length / WORD_PAGE_SIZE));
  const currentPage = Math.min(wordPage, pageCount - 1);
  const visibleWords = words.slice(currentPage * WORD_PAGE_SIZE, (currentPage + 1) * WORD_PAGE_SIZE);

  useEffect(() => {
    clipPreviewRef.current = null;
    setHistory(createHistory());
    setProposal(null);
    setCurrentTime(0);
    setWordPage(0);
    setClips([]);
    setClipSelected(new Set());
    setNotice(null);
  }, [selectedId]);

  const commitPlan = useCallback((nextPlan: TranscriptEditPlan) => {
    clipPreviewRef.current = null;
    setHistory((current) => {
      const next = clonePlan(nextPlan);
      if (samePlan(current.present, next)) return current;
      return { past: [...current.past, clonePlan(current.present)].slice(-100), present: next, future: [] };
    });
    setProposal(null);
  }, []);

  function undo() {
    setHistory((current) => {
      const previous = current.past[current.past.length - 1];
      if (!previous) return current;
      return {
        past: current.past.slice(0, -1),
        present: clonePlan(previous),
        future: [clonePlan(current.present), ...current.future].slice(0, 100),
      };
    });
    setProposal(null);
  }

  function redo() {
    setHistory((current) => {
      const next = current.future[0];
      if (!next) return current;
      return {
        past: [...current.past, clonePlan(current.present)].slice(-100),
        present: clonePlan(next),
        future: current.future.slice(1),
      };
    });
    setProposal(null);
  }

  async function upload(file: File | undefined) {
    if (!file) return;
    setBusy("upload");
    setUploadPercent(0);
    try {
      const source = await uploadMedia(projectId, file, { onProgress: setUploadPercent });
      await loadSources();
      setSelectedId(source.id);
      setNotice({ kind: "ok", text: "视频已导入" });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "视频导入失败" });
    } finally {
      setBusy(null);
      setUploadPercent(null);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  async function updateState(sourceId: string, body: Record<string, unknown>) {
    return updateTranscript(projectId, sourceId, body);
  }

  function transcribeChunk(
    worker: Worker,
    pcm: Float32Array,
    input: { model: LocalAsrModel; language: string; offsetSeconds: number; sourceDuration: number; chunkIndex: number; totalChunks: number },
  ): Promise<NonNullable<Awaited<ReturnType<typeof transcriptFromCheckpoint>>>> {
    return new Promise((resolve, reject) => {
      const base = (input.chunkIndex / input.totalChunks) * 99;
      const weight = 99 / input.totalChunks;
      worker.onmessage = (event: MessageEvent<AsrWorkerMessage>) => {
        const message = event.data;
        if (message.type === "device") {
          deviceRef.current = message.device;
          setDevice(message.device);
          if (message.fallback) setFallback(true);
          return;
        }
        if (message.type === "progress") {
          setPhase(message.phase);
          const fraction = message.phase === "loading" ? (message.progress / 100) * 0.2 : 0.2 + (message.progress / 100) * 0.8;
          progressRef.current = Math.max(progressRef.current, Math.min(99, Math.round(base + weight * fraction)));
          setProgress(progressRef.current);
          return;
        }
        if (message.type === "complete") resolve(message.transcript);
        if (message.type === "error") reject(new Error(message.error || "本地转写失败"));
      };
      worker.onerror = (event) => reject(new Error(event.message || "本地转写失败"));
      worker.postMessage(
        {
          type: "transcribe",
          audio: pcm,
          model: input.model,
          language: input.language,
          preferWebGpu: deviceRef.current !== "wasm",
          offsetSeconds: input.offsetSeconds,
          sourceDuration: input.sourceDuration,
          chunkIndex: input.chunkIndex,
        },
        [pcm.buffer],
      );
    });
  }

  function finishWorker() {
    abortRef.current = null;
    workerRef.current?.terminate();
    workerRef.current = null;
    if (heartbeatRef.current) clearInterval(heartbeatRef.current);
    heartbeatRef.current = null;
    setBusy(null);
    setPhase(null);
    setChunkState({ index: 0, total: 0 });
  }

  async function startTranscription() {
    if (!selected) return;
    let taskStarted = false;
    cancelRequestedRef.current = false;
    deviceRef.current = null;
    setDevice(null);
    setFallback(false);
    setProgress(0);
    progressRef.current = 0;
    setBusy("decode");
    try {
      const started = (await updateState(selected.id, { action: "start", model, language, resume: true })) as MediaSource & {
        resumeCheckpoint?: TranscriptCheckpoint | null;
      };
      taskStarted = true;
      // 续转会清空服务端的 error；同步回落本地列表，避免旧的「转写已中断」一直挂在正在运行的任务上。
      setSources((current) =>
        current.map((item) => (item.id === selected.id ? { ...item, ...started, error: null } : item)),
      );
      let checkpoint = started.resumeCheckpoint ?? null;
      const sourceDuration = selected.duration / 1000;
      let processedSeconds = checkpoint?.processedSeconds ?? 0;
      const totalChunks = Math.max(1, Math.ceil(sourceDuration / ASR_CHUNK_SECONDS));
      progressRef.current = Math.min(99, Math.round((processedSeconds / sourceDuration) * 100));
      setProgress(progressRef.current);
      if (checkpoint) setNotice({ kind: "ok", text: `从 ${formatDuration(processedSeconds)} 断点续转` });

      const worker = new Worker(new URL("../../../workers/asr.worker.ts", import.meta.url), { type: "module" });
      workerRef.current = worker;
      heartbeatRef.current = window.setInterval(() => {
        void updateState(selected.id, { action: "heartbeat", progress: progressRef.current }).catch(() => undefined);
      }, 15_000);

      while (processedSeconds < sourceDuration - 0.05) {
        if (cancelRequestedRef.current) break;
        const startSeconds = processedSeconds;
        const durationSeconds = Math.min(ASR_CHUNK_SECONDS, sourceDuration - startSeconds);
        const chunkIndex = Math.floor(startSeconds / ASR_CHUNK_SECONDS);
        setChunkState({ index: chunkIndex + 1, total: totalChunks });
        setBusy("decode");
        setPhase(null);
        const controller = new AbortController();
        abortRef.current = controller;
        const audio = await fetchAudioChunk(projectId, selected.id, startSeconds, durationSeconds, controller.signal);
        const pcm = decodeFloat32Pcm(audio);
        setBusy("transcribe");
        const chunk = await transcribeChunk(worker, pcm, {
          model,
          language,
          offsetSeconds: startSeconds,
          sourceDuration,
          chunkIndex,
          totalChunks,
        });
        const nextProcessed = Math.min(sourceDuration, startSeconds + durationSeconds);
        checkpoint = appendTranscriptChunk({
          checkpoint,
          chunk,
          sourceDuration,
          processedSeconds: nextProcessed,
          model,
          language,
        });
        const saved = (await updateState(selected.id, { action: "checkpoint", chunk, processedSeconds: nextProcessed })) as {
          progress?: number;
        };
        processedSeconds = nextProcessed;
        progressRef.current = Math.max(progressRef.current, saved.progress ?? Math.min(99, Math.round((processedSeconds / sourceDuration) * 100)));
        setProgress(progressRef.current);
      }

      if (cancelRequestedRef.current) return;
      if (!checkpoint) throw new Error("没有可写入的转写结果");
      const finalTranscript = transcriptFromCheckpoint(checkpoint);
      if (!finalTranscript) throw new Error("转写结果为空");
      await updateState(selected.id, { action: "complete", transcript: finalTranscript });
      await loadSources();
      setProgress(100);
      setNotice({ kind: "ok", text: "本地转写完成" });
      finishWorker();
    } catch (cause) {
      if (cancelRequestedRef.current || (cause instanceof DOMException && cause.name === "AbortError")) return;
      const message = cause instanceof Error ? cause.message : "本地转写失败";
      setNotice({ kind: "error", text: message });
      if (taskStarted) await updateState(selected.id, { action: "fail", error: message }).catch(() => undefined);
      finishWorker();
    }
  }

  async function cancelTranscription() {
    if (!selected || (busy !== "decode" && busy !== "transcribe")) return;
    cancelRequestedRef.current = true;
    abortRef.current?.abort();
    workerRef.current?.terminate();
    await updateState(selected.id, { action: "cancel" }).catch(() => undefined);
    finishWorker();
    await loadSources();
    setNotice({ kind: "ok", text: "已暂停转写；下次会从断点继续" });
  }

  function toggleWord(wordId: string) {
    commitPlan({
      ...plan,
      removedWordIds: removedIdSet.has(wordId)
        ? plan.removedWordIds.filter((id) => id !== wordId)
        : [...plan.removedWordIds, wordId],
    });
  }

  function seekTo(time: number) {
    clipPreviewRef.current = null;
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = Math.max(0, Math.min(time, transcript?.duration ?? time));
    setCurrentTime(Math.max(0, time));
  }

  function handleTimeUpdate() {
    const video = videoRef.current;
    if (!video) return;
    const previewRange = clipPreviewRef.current;
    if (previewRange) {
      if (video.currentTime >= previewRange.end) {
        video.pause();
        video.currentTime = previewRange.end;
      } else if (video.currentTime < previewRange.start) {
        video.currentTime = previewRange.start;
      }
      setCurrentTime(video.currentTime);
      return;
    }
    if (previewCuts && transcript) {
      const playable = nextPlayableSourceTime(video.currentTime, keepRanges);
      if (playable === null) {
        video.pause();
        return;
      }
      if (playable - video.currentTime > 0.02) {
        video.currentTime = playable;
        setCurrentTime(playable);
        return;
      }
    }
    setCurrentTime(video.currentTime);
  }

  function addCorrection() {
    if (!transcript || !correctionQuery.trim() || !correctionText.trim()) return;
    const matches = findTranscriptPhrase(transcript, correctionQuery.trim(), 1);
    if (matches.length === 0) {
      setNotice({ kind: "error", text: "没有在逐字稿里找到这段文字" });
      return;
    }
    const match = matches[0];
    const next = (plan.captionReplacements ?? []).filter(
      (item) => !item.wordIds.some((id) => match.wordIds.includes(id)),
    );
    commitPlan({ ...plan, captionReplacements: [...next, { wordIds: match.wordIds, text: correctionText.trim() }] });
    setCorrectionQuery("");
    setCorrectionText("");
  }

  function exportDraft(format: "srt" | "vtt" | "json") {
    if (!selected || !transcript) return;
    const stem = `clipforge-${selected.id.slice(0, 8)}-draft`;
    if (format === "json") {
      downloadText(
        `${stem}.json`,
        "application/json",
        JSON.stringify(
          {
            format: TRANSCRIPT_EDIT_FORMAT,
            operationId: crypto.randomUUID(),
            actor: "human",
            projectId,
            mediaId: selected.id,
            baseRevision: latestRevision,
            plan,
          },
          null,
          2,
        ),
      );
      return;
    }
    const cues = transcriptWordsToCues(transcript, keepRanges, plan);
    downloadText(`${stem}.${format}`, format === "srt" ? "application/x-subrip" : "text/vtt", format === "srt" ? buildSrt(cues) : buildVtt(cues));
  }

  function importDraft(file: File | undefined) {
    if (!file || !transcript) return;
    void file.text().then((text) => {
      try {
        const parsed = JSON.parse(text) as { plan?: unknown };
        const rawPlan = parsed && typeof parsed === "object" && "plan" in parsed ? parsed.plan : parsed;
        if (!rawPlan || typeof rawPlan !== "object" || (rawPlan as { version?: unknown }).version !== 1) {
          throw new Error("INVALID_PLAN");
        }
        commitPlan(sanitizeTranscriptEditPlan(rawPlan, new Set(transcript.words.map((word) => word.id)), transcript.duration));
        setNotice({ kind: "ok", text: "剪辑计划已导入" });
      } catch {
        setNotice({ kind: "error", text: "剪辑计划文件无效" });
      } finally {
        if (planInputRef.current) planInputRef.current.value = "";
      }
    });
  }

  async function exportTimeline(format: "otio" | "edl" | "csv") {
    if (!selected) return;
    setBusy("export");
    try {
      const blob = await exportTimelineDraft(projectId, selected.id, format, plan);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `clipforge-draft.${format}`;
      anchor.click();
      URL.revokeObjectURL(url);
      setNotice({ kind: "ok", text: `${format.toUpperCase()} 已导出` });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "时间线导出失败" });
    } finally {
      setBusy(null);
    }
  }

  async function previewEdit() {
    if (!selected) return;
    setBusy("preview");
    try {
      const result = await previewTranscriptEdit(projectId, selected.id, {
        operationId: crypto.randomUUID(),
        baseRevision: latestRevision,
        plan,
      });
      if (result.proposal) setProposal(result.proposal);
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "剪辑预演失败" });
    } finally {
      setBusy(null);
    }
  }

  async function applyEdit() {
    if (!selected) return;
    setBusy("render");
    try {
      const result = await applyTranscriptEdit(projectId, selected.id, {
        operationId: proposal?.operationId ?? crypto.randomUUID(),
        baseRevision: latestRevision,
        plan,
      });
      setProposal(null);
      await loadSources();
      setNotice({ kind: "ok", text: result.status === "queued" ? "剪辑任务已排队，完成后会出现在输出列表" : "剪辑已提交" });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "剪辑提交失败" });
    } finally {
      setBusy(null);
    }
  }

  async function cancelEdit(editId: string) {
    if (!selected) return;
    try {
      await cancelTranscriptEdit(projectId, selected.id, editId);
      await loadSources();
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "取消失败" });
    }
  }

  async function retryEdit(editId: string) {
    if (!selected) return;
    try {
      await retryTranscriptEdit(projectId, selected.id, editId);
      await loadSources();
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "重试失败" });
    }
  }

  async function findClips() {
    if (!selected) return;
    setClipsBusy(true);
    try {
      const result = await listTranscriptClips(projectId, selected.id, {
        ...(clipQuery.trim() ? { query: clipQuery.trim() } : {}),
        targetSeconds: clipTarget,
        limit: 6,
      });
      setClips(result.candidates ?? []);
      setClipSelected(new Set((result.candidates ?? []).slice(0, 3).map((clip) => clip.id)));
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "片段建议失败" });
    } finally {
      setClipsBusy(false);
    }
  }

  function previewClip(clip: TranscriptClipCandidate) {
    const video = videoRef.current;
    if (!video) return;
    clipPreviewRef.current = clip.sourceRange;
    video.currentTime = clip.sourceRange.start;
    setCurrentTime(clip.sourceRange.start);
    void video.play().catch(() => setNotice({ kind: "ok", text: "请手动点击播放预览片段" }));
  }

  async function renderSelectedClips() {
    if (!selected || !transcript || clipSelected.size === 0) return;
    const items = clips
      .filter((clip) => clipSelected.has(clip.id))
      .map((clip) => ({ label: clip.text.slice(0, 40) || clip.id, plan: clip.plan }));
    if (items.length === 0) return;
    setBusy("render");
    try {
      await applyTranscriptBatch(projectId, selected.id, {
        action: "apply",
        batchId: crypto.randomUUID(),
        baseRevision: latestRevision,
        items,
      });
      await loadSources();
      setNotice({ kind: "ok", text: `已提交 ${items.length} 条片段输出` });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "批量输出失败" });
    } finally {
      setBusy(null);
    }
  }

  if (loading) {
    return (
      <section className="ai-video-page" aria-label="逐字稿">
        <div className="ai-video-empty" role="status">
          <span className="ai-video-empty-icon">◌</span>
          <strong>正在加载逐字稿…</strong>
        </div>
      </section>
    );
  }

  if (loadError) {
    return (
      <section className="ai-video-page" aria-label="逐字稿">
        <div className="ai-video-empty" role="alert">
          <span className="ai-video-empty-icon">!</span>
          <strong>{loadError}</strong>
          <button type="button" onClick={() => onNavigate("/projects")}>返回我的项目</button>
        </div>
      </section>
    );
  }

  return (
    <section className="ai-video-page" aria-label="逐字稿">
      {project && <ProjectHeader project={project} step="transcript" onNavigate={onNavigate} />}

      {notice && (
        <div className={`ai-video-notice${notice.kind === "error" ? " is-error" : ""}`} role="status">
          <span>{notice.text}</span>
          <button type="button" aria-label="关闭提示" onClick={() => setNotice(null)}>×</button>
        </div>
      )}

      <div className="ai-video-transcript-layout">
        <section className="ai-video-form-card" aria-label="素材与转写">
          <div className="ai-video-form-head">
            <h3>导入视频与本地转写</h3>
            <p>Whisper 在浏览器 Worker 内分块运行；长视频会每 5 分钟落一次断点。</p>
          </div>
          <div className="ai-video-settings-actions">
            <button type="button" className="is-secondary" disabled={busy !== null} onClick={() => inputRef.current?.click()}>
              {busy === "upload" ? `导入中…${uploadPercent ?? 0}%` : "导入视频"}
            </button>
            <input
              ref={inputRef}
              type="file"
              accept="video/mp4,video/quicktime,video/webm,video/x-matroska,video/x-m4v,.mp4,.mov,.webm,.mkv,.m4v"
              hidden
              onChange={(event) => void upload(event.target.files?.[0])}
            />
            {selected && transcript && (
              <>
                <button type="button" disabled={busy !== null} onClick={() => void previewEdit()}>
                  {busy === "preview" ? "预演中…" : "预演剪辑"}
                </button>
                <button type="button" disabled={busy !== null} onClick={() => void applyEdit()}>
                  {busy === "render" ? "提交中…" : proposal ? "确认输出剪辑" : "直接输出剪辑"}
                </button>
              </>
            )}
          </div>

          {sources.length === 0 ? (
            <p className="ai-video-settings-message">还没有导入视频。支持 MP4 / MOV / WebM / MKV / M4V，单文件 ≤1GB、≤2 小时。</p>
          ) : (
            <div className="ai-video-source-list">
              {sources.map((source) => (
                <button
                  key={source.id}
                  type="button"
                  className={`ai-video-source${source.id === selectedId ? " is-active" : ""}`}
                  onClick={() => setSelectedId(source.id)}
                >
                  <strong>{source.originalName}</strong>
                  <span>
                    {formatDuration(source.duration / 1000)} · {formatBytes(source.sizeBytes)} ·{" "}
                    {source.status === "ready" ? "已转写" : source.status === "transcribing" ? `转写中 ${source.progress}%` : source.status === "failed" ? "失败" : "待转写"}
                  </span>
                </button>
              ))}
            </div>
          )}

          {selected && (
            <>
              <div className="ai-video-settings-grid">
                <label className="ai-video-field">
                  本地模型
                  <select value={model} disabled={busy !== null} onChange={(event) => setModel(event.target.value as LocalAsrModel)}>
                    {LOCAL_ASR_MODELS.map((item) => (
                      <option key={item.id} value={item.id}>{item.label} · {item.description}</option>
                    ))}
                  </select>
                </label>
                <label className="ai-video-field">
                  语言
                  <select value={language} disabled={busy !== null} onChange={(event) => setLanguage(event.target.value)}>
                    <option value="auto">自动</option>
                    <option value="zh">中文</option>
                    <option value="en">英语</option>
                    <option value="ja">日语</option>
                    <option value="ko">韩语</option>
                  </select>
                </label>
              </div>

              <div className="ai-video-settings-actions">
                <button type="button" disabled={busy !== null || !selected.hasAudio} onClick={() => void startTranscription()}>
                  {busy === "decode" || busy === "transcribe"
                    ? "转写中…"
                    : selected.checkpoint?.resumable
                      ? `从 ${formatDuration(selected.checkpoint.processedSeconds)} 续转`
                      : transcript
                        ? "重新转写"
                        : "开始本地转写"}
                </button>
                {(busy === "decode" || busy === "transcribe") && (
                  <button type="button" className="is-secondary" onClick={() => void cancelTranscription()}>
                    暂停
                  </button>
                )}
                {selected.status === "transcribing" && selected.progress > 0 && (
                  <span className="ai-video-settings-message">
                    {selected.progress}%{selected.device ? ` · ${selected.device}` : ""}
                    {chunkState.total ? ` · 分块 ${chunkState.index}/${chunkState.total}` : ""}
                    {phase ? ` · ${phase === "loading" ? "加载模型" : "转写"}` : ""}
                  </span>
                )}
                {device && <span className="ai-video-settings-message">{fallback ? "WebGPU 失败，已回退 WASM" : `设备：${device}`}</span>}
              </div>

              {(busy === "decode" || busy === "transcribe" || progress > 0) && (
                <div className="ai-video-progress-track" aria-label="转写进度">
                  <i style={{ width: `${Math.max(2, progress)}%` }} />
                </div>
              )}
              {!selected.hasAudio && <p className="ai-video-field-error">这个视频没有音轨，无法转写。</p>}
              {selected.error && <p className="ai-video-field-error">{selected.error}</p>}
            </>
          )}
        </section>

        {selected && transcript && (
          <>
            <section className="ai-video-form-card" aria-label="词级编辑">
              <div className="ai-video-form-head">
                <h3>词级编辑</h3>
                <p>
                  成片 {formatDuration(editedSeconds)}（原 {formatDuration(transcript.duration)}）· 已删{" "}
                  {plan.removedWordIds.length} 词 / {formatDuration(removedSeconds)}
                  {plan.captionReplacements?.length ? ` · ${plan.captionReplacements.length} 处字幕校正` : ""}
                </p>
              </div>
              <div className="ai-video-settings-actions">
                <button type="button" className="is-secondary" disabled={history.past.length === 0} onClick={undo}>撤销</button>
                <button type="button" className="is-secondary" disabled={history.future.length === 0} onClick={redo}>重做</button>
                <button type="button" className="is-secondary" disabled={remainingFillerIds.length === 0} onClick={() => commitPlan({ ...plan, removedWordIds: [...plan.removedWordIds, ...remainingFillerIds] })}>
                  标记 {remainingFillerIds.length} 个语气词
                </button>
                <button type="button" className="is-secondary" onClick={() => commitPlan(DEFAULT_TRANSCRIPT_EDIT_PLAN)}>重置</button>
                <label className="ai-video-check">
                  <input type="checkbox" checked={plan.removeSilence} onChange={(event) => commitPlan({ ...plan, removeSilence: event.target.checked })} />
                  删除静音
                </label>
                <label className="ai-video-check">
                  <input type="checkbox" checked={plan.burnSubtitles} onChange={(event) => commitPlan({ ...plan, burnSubtitles: event.target.checked })} />
                  输出时烧录字幕
                </label>
                <label className="ai-video-check">
                  <input type="checkbox" checked={previewCuts} onChange={(event) => setPreviewCuts(event.target.checked)} />
                  预览跳过删减
                </label>
              </div>

              <div className="ai-video-settings-grid">
                <label className="ai-video-field">
                  起止裁剪（可选）
                  <span className="ai-video-settings-actions">
                    <input
                      aria-label="起始秒"
                      inputMode="decimal"
                      value={plan.sourceRange?.start ?? ""}
                      onChange={(event) => {
                        const start = Number(event.target.value);
                        if (!Number.isFinite(start)) return;
                        commitPlan({ ...plan, sourceRange: { start, end: plan.sourceRange?.end ?? transcript.duration } });
                      }}
                    />
                    <input
                      aria-label="结束秒"
                      inputMode="decimal"
                      value={plan.sourceRange?.end ?? ""}
                      onChange={(event) => {
                        const end = Number(event.target.value);
                        if (!Number.isFinite(end)) return;
                        commitPlan({ ...plan, sourceRange: { start: plan.sourceRange?.start ?? 0, end } });
                      }}
                    />
                    {plan.sourceRange && (
                      <button type="button" className="is-secondary" onClick={() => {
                        const { sourceRange: _drop, ...rest } = plan;
                        commitPlan(rest);
                      }}>
                        清除
                      </button>
                    )}
                  </span>
                </label>
                <label className="ai-video-field">
                  字幕校正（查找原文 → 替换）
                  <span className="ai-video-settings-actions">
                    <input aria-label="查找原文" value={correctionQuery} onChange={(event) => setCorrectionQuery(event.target.value)} />
                    <input aria-label="替换为" value={correctionText} onChange={(event) => setCorrectionText(event.target.value)} />
                    <button type="button" className="is-secondary" onClick={addCorrection}>校正</button>
                  </span>
                </label>
              </div>

              <div className="ai-video-word-editor">
                {visibleWords.map((word) => (
                  <button
                    key={word.id}
                    type="button"
                    className={`${removedIdSet.has(word.id) ? "is-removed" : ""}${activeWordId === word.id ? " is-active" : ""}`}
                    title={`${word.start.toFixed(2)}s – ${word.end.toFixed(2)}s`}
                    onClick={() => toggleWord(word.id)}
                    onDoubleClick={() => seekTo(word.start)}
                  >
                    {word.text}
                  </button>
                ))}
              </div>
              {pageCount > 1 && (
                <div className="ai-video-material-pager">
                  <span>第 {currentPage + 1}/{pageCount} 页 · 共 {words.length} 词</span>
                  <div>
                    <button type="button" className="is-secondary" disabled={currentPage === 0} onClick={() => setWordPage(currentPage - 1)}>上一页</button>
                    <button type="button" className="is-secondary" disabled={currentPage + 1 >= pageCount} onClick={() => setWordPage(currentPage + 1)}>下一页</button>
                  </div>
                </div>
              )}
              <p className="ai-video-settings-message">单击词条删除/恢复；双击定位播放。字幕校正只改字幕文本，不改时间轴。</p>
            </section>

            <section className="ai-video-form-card" aria-label="预览与导出">
              <div className="ai-video-form-head">
                <h3>预览与导出</h3>
                <p>当前 {formatDuration(currentTime)} / {formatDuration(transcript.duration)}</p>
              </div>
              <video
                ref={videoRef}
                className="ai-video-compose-video"
                src={aiVideoMediaUrl(selected.url) ?? undefined}
                controls
                playsInline
                preload="metadata"
                onTimeUpdate={handleTimeUpdate}
              />
              <div className="ai-video-settings-actions">
                <button type="button" className="is-secondary" onClick={() => exportDraft("srt")}>导出 SRT</button>
                <button type="button" className="is-secondary" onClick={() => exportDraft("vtt")}>导出 VTT</button>
                <button type="button" className="is-secondary" onClick={() => exportDraft("json")}>导出剪辑 JSON</button>
                <button type="button" className="is-secondary" onClick={() => planInputRef.current?.click()}>导入剪辑 JSON</button>
                <input ref={planInputRef} type="file" accept="application/json,.json" hidden onChange={(event) => importDraft(event.target.files?.[0])} />
              </div>
              <div className="ai-video-settings-actions">
                {(["otio", "edl", "csv"] as const).map((format) => (
                  <button key={format} type="button" className="is-secondary" disabled={busy === "export"} onClick={() => void exportTimeline(format)}>
                    {busy === "export" ? "导出中…" : `导出 ${format.toUpperCase()}`}
                  </button>
                ))}
              </div>
            </section>

            <section className="ai-video-form-card" aria-label="剪辑输出">
              <div className="ai-video-form-head">
                <h3>剪辑输出</h3>
                <p>非破坏式渲染：每次输出都是新版本，原片与逐字稿不被改写。</p>
              </div>
              {proposal && (
                <div className="ai-video-verdict is-warn">
                  <strong>剪辑预演 R{proposal.latestRevision} → R{proposal.nextRevision}</strong>
                  <p>
                    成片 {formatDuration(proposal.summary.outputDuration)} · 删除 {proposal.summary.removedWordCount} 词 ·{" "}
                    {proposal.summary.removedRangeCount} 段
                    {proposal.plan.burnSubtitles ? "" : " · 不烧录字幕"}
                    {proposal.conflict ? " · 版本已变化，请重新预演" : ""}
                  </p>
                  {proposal.summary.removedTextPreview && <p className="ai-video-asset-desc">删除预览：{proposal.summary.removedTextPreview}</p>}
                  <div className="ai-video-settings-actions">
                    <button type="button" disabled={proposal.conflict || busy !== null} onClick={() => void applyEdit()}>
                      确认输出
                    </button>
                    <button type="button" className="is-secondary" onClick={() => setProposal(null)}>取消</button>
                  </div>
                </div>
              )}
              {selected.edits.length === 0 ? (
                <p className="ai-video-settings-message">还没有剪辑输出。</p>
              ) : (
                <ul className="ai-video-pending-tasks">
                  {selected.edits.map((edit) => (
                    <li key={edit.id}>
                      <span>
                        R{edit.revision} · {edit.status}
                        {edit.progress != null && edit.status === "rendering" ? ` ${edit.progress}%` : ""}
                        {edit.summary ? ` · ${formatDuration(edit.summary.outputDuration)}` : ""}
                      </span>
                      <span className="ai-video-settings-actions">
                        {edit.composition?.outputUrl && (
                          <a href={aiVideoMediaUrl(edit.composition.outputUrl) ?? "#"} target="_blank" rel="noreferrer">预览</a>
                        )}
                        {edit.composition?.downloadUrl && (
                          <a href={aiVideoMediaUrl(edit.composition.downloadUrl) ?? "#"} download>下载</a>
                        )}
                        {(edit.status === "queued" || edit.status === "rendering") && (
                          <button type="button" className="is-secondary" onClick={() => void cancelEdit(edit.id)}>取消</button>
                        )}
                        {edit.status === "failed" && (
                          <button type="button" className="is-secondary" onClick={() => void retryEdit(edit.id)}>重试</button>
                        )}
                      </span>
                      {edit.error && <p className="ai-video-field-error">{edit.error}</p>}
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="ai-video-form-card" aria-label="片段建议与批量输出">
              <div className="ai-video-form-head">
                <h3>片段建议 / 批量输出</h3>
                <p>按句子边界与停顿从逐字稿挑出可发布片段；勾选后一次批量渲染。</p>
              </div>
              <div className="ai-video-settings-actions">
                <input aria-label="关键词" placeholder="关键词（可选）" value={clipQuery} onChange={(event) => setClipQuery(event.target.value)} />
                <select aria-label="目标时长" value={clipTarget} onChange={(event) => setClipTarget(Number(event.target.value))}>
                  {[15, 30, 45, 60, 90].map((seconds) => (
                    <option key={seconds} value={seconds}>{seconds}s</option>
                  ))}
                </select>
                <button type="button" className="is-secondary" disabled={clipsBusy} onClick={() => void findClips()}>
                  {clipsBusy ? "筛选中…" : "找片段"}
                </button>
                <button type="button" disabled={clipSelected.size === 0 || busy !== null} onClick={() => void renderSelectedClips()}>
                  批量输出 {clipSelected.size} 条
                </button>
              </div>
              {clips.length > 0 && (
                <ul className="ai-video-pending-tasks">
                  {clips.map((clip) => (
                    <li key={clip.id}>
                      <label className="ai-video-check">
                        <input
                          type="checkbox"
                          checked={clipSelected.has(clip.id)}
                          onChange={(event) =>
                            setClipSelected((current) => {
                              const next = new Set(current);
                              if (event.target.checked) next.add(clip.id);
                              else next.delete(clip.id);
                              return next;
                            })
                          }
                        />
                        {formatDuration(clip.sourceRange.start)}–{formatDuration(clip.sourceRange.end)} · {clip.duration.toFixed(1)}s
                      </label>
                      <span className="ai-video-asset-desc">{clip.text}</span>
                      <button type="button" className="is-secondary" onClick={() => previewClip(clip)}>预览</button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
      </div>
    </section>
  );
}
