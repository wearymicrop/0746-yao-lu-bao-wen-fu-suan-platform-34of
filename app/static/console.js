const el = (id) => document.getElementById(id);
const states = { proven: "达标", cold: "失温", unproven: "缺证" };
const causes = {
  NO_READING: "读数失采",
  BAD_READING: "读数无效",
  DRIFT_LIMIT: "偏移超限",
  READING_CONFLICT: "同点读数冲突",
  RECIPE_OVERLAP: "配方重叠",
  RECIPE_GAP: "配方缺失",
  CALIBRATION_GAP: "校准缺失",
  CALIBRATION_OVERLAP: "校准重叠",
};
const rulings = {
  EFFECTIVE: "正式版生效",
  TRIAL: "试签版生效",
  UNRESOLVED: "重叠未决",
  MISSING: "缺少配方",
};
const dispositions = { CHOSEN: "采用", PENDING: "待核", IGNORED: "未采用" };
const kinds = { formal: "正式", trial: "试签" };
const time = (value) =>
  new Date(value * 1000).toLocaleTimeString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour12: false,
  });
const temp = (value) =>
  value == null ? "—" : `${(value / 1000).toFixed(1)} °C`;
let snapshot,
  source = "dirty",
  serial = 0,
  archived,
  buttons = [];
function make(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw Error(data.message || "请求失败，请重试");
  return data;
}
async function load(key) {
  const current = ++serial;
  el("reading-status").textContent = "正在复算";
  el("sign-form").querySelector("button").disabled = true;
  try {
    const parts = [
      "intake",
      "calibration",
      "alignment",
      "soak",
      "gaps",
      "reconcile",
    ];
    const result = await Promise.all(
      parts.map((part) => api(`/api/${part}/${encodeURIComponent(key)}`)),
    );
    if (current !== serial) return;
    source = key;
    snapshot = Object.fromEntries(parts.map((part, i) => [part, result[i]]));
    el("batch").textContent = snapshot.intake.plan.batch;
    el("reading-status").textContent = snapshot.soak.goal_met
      ? "保温达标"
      : "待工艺复核";
    el("run-actions").hidden = true;
    el("run-feedback").textContent = "";
    archived = undefined;
    render();
    el("sign-form").querySelector("button").disabled = false;
  } catch (error) {
    if (current === serial) el("reading-status").textContent = error.message;
  }
}
function inspect(index) {
  const item = snapshot.soak.sections[index];
  if (!item) return;
  buttons.forEach((button, i) =>
    button.setAttribute("aria-pressed", String(i === index)),
  );
  const version = item.version
    ? `${item.version} · ${rulings[item.recipe_status] || ""}`
    : `配方待核${item.retained_version ? ` · 沿用 ${item.retained_version}` : ""}`;
  const title = make(
    "h3",
    `${time(item.start)}–${time(item.end)} · ${states[item.state]} · ${version}`,
  );
  const grid = make("div", undefined, "zone-grid");
  for (const z of item.zones) {
    const cell = make("div", z.zone, "zone-card");
    cell.append(
      make("strong", temp(z.value_mc)),
      make(
        "small",
        `目标 ${temp(z.target_mc)} · 读数行 ${z.reading_rows.join("、") || "—"}`,
      ),
      make(
        "small",
        z.code === "BELOW_TARGET"
          ? "低于目标"
          : causes[z.code] || `校准行 ${z.calibration_row ?? "—"}`,
      ),
    );
    grid.append(cell);
  }
  const notes = make("div", undefined, "segment-notes");
  notes.append(
    make("p", `本段裁决：${item.recipe_decision}`, "note-strong"),
    make(
      "p",
      item.streak_index
        ? `位于第 ${item.streak_index} 个连续段内 · 前接 ${item.run_before_s} 秒 · 后续 ${item.run_after_s} 秒 · 该段共 ${item.streak_s} 秒（${item.version}）`
        : `本段不连续 · 上一连续段 ${item.previous_streak_s} 秒 · 下一连续段 ${item.next_streak_s} 秒`,
    ),
  );
  if (item.recipe_status !== "EFFECTIVE" && item.retained_version) {
    notes.append(
      make(
        "p",
        `未决期间保留旧版 ${item.retained_version}（来源行 ${item.retained_rows.join("、")}）：按旧版目标判为 ${states[item.retained_state] || "未定"}，旧结论不清空`,
        "note-retained",
      ),
    );
  }
  const sources =
    item.recipe_sources.length > 1 ? make("ul", undefined, "sources") : null;
  if (sources) {
    for (const source of item.recipe_sources) {
      sources.append(
        make(
          "li",
          `${source.version} · ${kinds[source.kind] || source.kind} · 行 ${source.rows.join("、")} · ${dispositions[source.disposition] || source.disposition}`,
        ),
      );
    }
  }
  el("segment").replaceChildren(title, notes, grid);
  if (sources) el("segment").append(sources);
}

function ruling() {
  const ruling = snapshot.soak.adjudication;
  if (!ruling) return el("ruling").replaceChildren();
  const versions = ruling.effective
    .map((run) => run.version)
    .filter(Boolean)
    .join(" → ");
  const head = make(
    "p",
    ruling.status === "CLEAR"
      ? `配方已裁决：${versions}`
      : `${ruling.decision} · 未决 ${ruling.pending_s} 秒`,
    `ruling-head ${ruling.status.toLowerCase()}`,
  );
  const nodes = [head];
  if (ruling.status !== "CLEAR") {
    nodes.push(
      ...ruling.windows.map((window) => {
        const sources = snapshot.soak.sections
          .filter((item) => item.start >= window.start && item.end <= window.end)
          .flatMap((item) => item.pending_sources)
          .filter(
            (source, index, all) =>
              all.findIndex(
                (other) => other.rows.join() === source.rows.join(),
              ) === index,
          );
        const strip = make("div", undefined, "ruling-window");
        strip.append(
          make(
            "span",
            `${time(window.start)}–${time(window.end)} · ${window.seconds} 秒`,
          ),
          make(
            "small",
            `未决来源：${sources
              .map((source) => `${source.version}（行 ${source.rows.join("、")}）`)
              .join("、")}`,
          ),
        );
        return strip;
      }),
    );
    if (ruling.retained?.version) {
      nodes.push(
        make(
          "p",
          `保留旧版结论：${ruling.retained.version}（行 ${ruling.retained.rows.join("、")}）此前已连续 ${ruling.retained.streak_s} 秒，按旧版本段判为 ${states[ruling.retained.state] || "未定"}`,
          "ruling-retained",
        ),
      );
    }
  }
  el("ruling").replaceChildren(...nodes);
}
function render() {
  const { reconcile: r, intake, soak, gaps } = snapshot;
  el("metrics").replaceChildren(
    ...[
      ["计划", r.planned_s],
      ["达标累计", r.proven_s],
      ["失温", r.cold_s],
      ["缺证", r.unproven_s],
      ["最长连续", r.longest_s],
    ].map(([label, value]) => {
      const tile = make("div", undefined, "kpi");
      tile.append(
        make("span", label),
        make("strong", value),
        make("small", " 秒"),
      );
      return tile;
    }),
  );
  el("balance").textContent =
    r.status === "MATCHED"
      ? `时长和行数已对平 · 连续目标 ${soak.goal_s} 秒${
          soak.adjudication.status === "CLEAR"
            ? ""
            : ` · 配方未决 ${soak.unresolved_s} 秒，保留旧版结论`
        }`
      : "炉批存在差异";
  ruling();
  buttons = soak.sections.map((item, i) => {
    const button = make("button", undefined, `slot ${item.state}`);
    button.style.setProperty("--part", item.seconds);
    button.title = `${time(item.start)} ${states[item.state]} · ${
      item.version || "配方待核"
    }`;
    button.setAttribute("aria-label", button.title);
    button.onclick = () => inspect(i);
    return button;
  });
  el("track-bars").replaceChildren(...buttons);
  const axis = Array.from({ length: 5 }, (_, i) =>
    make(
      "time",
      time(intake.plan.start + Math.floor((r.planned_s * i) / 4)).slice(0, 5),
    ),
  );
  el("track-axis").replaceChildren(...axis);
  inspect(0);
  el("gap-count").textContent = `${gaps.gap_count} 段`;
  el("gap-list").replaceChildren(
    ...(gaps.gaps.length
      ? gaps.gaps.map((row) => {
          const button = make("button", undefined, "gap");
          const summary = make(
            "span",
            row.codes.map((code) => causes[code] || code).join("、"),
          );
          summary.append(make("small", `${time(row.start)} · ${row.decision}`));
          if (row.retained_version) {
            summary.append(
              make(
                "small",
                `保留 ${row.retained_version} · 未决来源 ${row.pending_sources
                  .map((source) => source.version)
                  .join("、")}`,
                "gap-retained",
              ),
            );
          }
          button.append(summary, make("time", `${row.seconds} 秒`));
          button.onclick = () =>
            inspect(
              soak.sections.findIndex((item) => item.start === row.start),
            );
          return button;
        })
      : [make("p", "保温期间无断档", "empty")]),
  );
  evidence();
}
function evidence() {
  if (!snapshot) return;
  const { intake, calibration, alignment } = snapshot;
  let columns, entries;
  switch (el("evidence").value) {
    case "calibration":
      columns = ["热区", "采集时间", "读数行", "校准后", "校准行"];
      entries = calibration.corrected.map((row) => [
        row.zone,
        time(row.time),
        row.row,
        temp(row.corrected_mc),
        row.calibration_row,
      ]);
      entries.unshift(
        ...calibration.isolated.map((row) => [
          row.raw?.zone || "—",
          "隔离",
          row.row,
          row.message,
          "—",
        ]),
      );
      break;
    case "alignment":
      columns = ["热区", "起点", "终点", "有效读数", "读数行"];
      entries = Object.entries(alignment.zones).flatMap(([zone, spans]) =>
        spans.map((row) => [
          zone,
          time(row.start),
          time(row.end),
          temp(row.value_mc),
          row.reading_rows.join("、"),
        ]),
      );
      break;
    default:
      columns = ["热区", "采集时间", "来源行", "采集值", "判定"];
      entries = intake.points.map((row) => [
        row.zone,
        time(row.time),
        row.row,
        temp(row.value_mc),
        "采信",
      ]);
      entries.unshift(
        ...[...intake.isolated, ...intake.notices].map((row) => [
          row.raw?.zone || "—",
          row.raw?.time?.slice?.(11, 19) || "—",
          row.row,
          row.raw?.value ?? "—",
          row.message,
        ]),
      );
  }
  const table = make("table");
  const head = table.createTHead().insertRow();
  columns.forEach((label) => head.append(make("th", label)));
  const body = table.createTBody();
  entries.forEach((entry) => {
    const row = body.insertRow();
    entry.forEach((value, index) =>
      row.append(make("td", value, index === 0 ? "" : "num")),
    );
  });
  el("evidence-table").replaceChildren(table);
}
el("evidence").onchange = evidence;
el("sample").onchange = (event) => load(event.target.value);
el("upload").onchange = async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    const result = await api("/api/sources", { method: "POST", body: file });
    const option = new Option(file.name, result.source, true, true);
    el("sample").add(option);
    load(result.source);
  } catch (error) {
    el("reading-status").textContent = error.message;
  } finally {
    event.target.value = "";
  }
};
el("sign-form").onsubmit = async (event) => {
  event.preventDefault();
  const requested = source;
  const button = event.submitter;
  button.disabled = true;
  try {
    const result = await api("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source, operator: el("operator").value }),
    });
    if (source !== requested) return;
    archived = result.id;
    el("run-feedback").textContent =
      `${result.reused ? "归档已存在" : "归档成功"} · ${result.approval.decision}`;
    el("export").href = `/api/runs/${archived}/export`;
    el("run-actions").hidden = false;
  } catch (error) {
    el("run-feedback").textContent = error.message;
  } finally {
    button.disabled = false;
  }
};
el("rerun").onclick = async () => {
  try {
    const result = await api(`/api/runs/${archived}/rerun`, { method: "POST" });
    el("run-feedback").textContent =
      `重跑一致 · ${result.manifest.digest.slice(0, 12)}`;
  } catch (error) {
    el("run-feedback").textContent = error.message;
  }
};
load(source);
