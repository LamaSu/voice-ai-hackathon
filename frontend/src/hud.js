/**
 * Contract 4 (`metrics`) rendering — the number judges ask about.
 *
 * Everything here is driven by `metrics` messages from the bot. The HUD never
 * times anything itself: a stopwatch in the browser would measure the network
 * and the audio element as well, and we would not be able to defend the figure.
 */

// Under 500 ms feels conversational; over 1.5 s feels like a phone tree.
const GOOD_MS = 500;
const POOR_MS = 1500;

export function gradeFor(ms) {
  if (ms == null) return "unknown";
  if (ms < GOOD_MS) return "good";
  if (ms < POOR_MS) return "fair";
  return "poor";
}

export function median(values) {
  if (!values.length) return null;
  const ordered = [...values].sort((a, b) => a - b);
  const mid = Math.floor(ordered.length / 2);
  return ordered.length % 2
    ? ordered[mid]
    : (ordered[mid - 1] + ordered[mid]) / 2;
}

export class LatencyHUD {
  constructor(root = document) {
    this.samples = [];
    this.contentSamples = [];
    this.el = {
      latest: root.getElementById("latest"),
      median: root.getElementById("median"),
      best: root.getElementById("best"),
      worst: root.getElementById("worst"),
      count: root.getElementById("count"),
      contentRow: root.getElementById("contentRow"),
      latestContent: root.getElementById("latestContent"),
      audioLabel: root.getElementById("audioLabel"),
      stages: root.getElementById("stages"),
      spark: root.getElementById("spark"),
    };
  }

  /** Handle one Contract 4 `metrics` payload. */
  record(payload) {
    const ms = payload?.end_of_speech_to_first_audio_ms;
    if (typeof ms !== "number" || Number.isNaN(ms)) return;

    this.samples.push(ms);
    const grade = gradeFor(ms);

    // Only show the second line once a filler has actually separated the two,
    // so a run without fillers stays a single honest number.
    const contentMs = payload.end_of_speech_to_first_content_ms;
    const hasFiller = typeof contentMs === "number" && Math.round(contentMs) !== Math.round(ms);
    if (hasFiller) {
      this.contentSamples.push(contentMs);
      this.el.latestContent.textContent = Math.round(contentMs);
      this.el.latestContent.className = `latest-sub grade-${gradeFor(contentMs)}`;
      this.el.contentRow.removeAttribute("hidden");
      this.el.audioLabel.textContent = payload.filler
        ? `to first audio (${payload.filler})`
        : "to first audio";
    }

    this.el.latest.textContent = Math.round(ms);
    this.el.latest.className = `latest grade-${grade}`;
    this.el.median.textContent = `${Math.round(median(this.samples))} ms`;
    this.el.best.textContent = `${Math.round(Math.min(...this.samples))} ms`;
    this.el.worst.textContent = `${Math.round(Math.max(...this.samples))} ms`;
    this.el.count.textContent = String(this.samples.length);

    this.renderStages(payload.stages || {}, payload.detail?.labels || {}, ms);
    this.renderSpark();
  }

  renderStages(stages, labels, total) {
    const entries = Object.entries(stages).filter(([, v]) => v > 0);
    if (!entries.length) {
      this.el.stages.innerHTML = '<li class="empty">No stage breakdown reported.</li>';
      return;
    }
    entries.sort((a, b) => b[1] - a[1]);
    this.el.stages.innerHTML = entries
      .map(([key, valueMs]) => {
        const pct = total > 0 ? Math.min(100, (valueMs / total) * 100) : 0;
        const label = labels[key] || key;
        return `<li>
            <span class="stage-name" title="${escapeHtml(label)}">${escapeHtml(key)}</span>
            <span class="stage-bar"><span style="width:${pct.toFixed(1)}%"></span></span>
            <span class="stage-ms">${Math.round(valueMs)} ms</span>
          </li>`;
      })
      .join("");
  }

  renderSpark() {
    const c = this.el.spark;
    if (!c?.getContext) return;
    const ctx = c.getContext("2d");
    const recent = this.samples.slice(-40);
    ctx.clearRect(0, 0, c.width, c.height);
    if (!recent.length) return;

    const max = Math.max(POOR_MS, ...recent);
    const barW = c.width / recent.length;

    // Reference line at the 500 ms target.
    const targetY = c.height - (GOOD_MS / max) * c.height;
    ctx.strokeStyle = "rgba(255,255,255,0.35)";
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(0, targetY);
    ctx.lineTo(c.width, targetY);
    ctx.stroke();
    ctx.setLineDash([]);

    recent.forEach((ms, i) => {
      const h = Math.max(2, (ms / max) * c.height);
      ctx.fillStyle = colorFor(gradeFor(ms));
      ctx.fillRect(i * barW + 1, c.height - h, Math.max(1, barW - 2), h);
    });
  }
}

function colorFor(grade) {
  return { good: "#37d67a", fair: "#f5a623", poor: "#e5484d" }[grade] || "#888";
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}
