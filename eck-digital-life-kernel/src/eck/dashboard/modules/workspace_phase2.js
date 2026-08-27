import { formatBytes, formatTime, concise, escapeHtml } from "./workspace_components.js";
export class ResultCenterComponent {
    render(data, append = false) {
        const count = document.querySelector("#result-count");
        if (count)
            count.textContent = `${data.page.total} 項可追溯成果`;
        const target = document.querySelector("#result-catalog");
        if (!target)
            return;
        const html = data.items.map((item) => this.card(item)).join("");
        target.innerHTML = append ? target.innerHTML + html : html;
        if (!target.innerHTML) {
            target.innerHTML = '<div class="empty-state wide"><p>尚無可索引成果；目錄不會憑空建立成果。</p></div>';
        }
        const more = document.querySelector("#load-more-results");
        if (more)
            more.hidden = data.page.next_offset === null;
    }
    detail(item) {
        const usages = item.skills.map((usage) => `
      <li><b>${escapeHtml(usage.skill_name)}</b><span>v${escapeHtml(usage.skill_version)}</span><small>${escapeHtml(usage.verification_status)}</small></li>
    `).join("");
        return `
      <header class="project-detail-head"><div><span class="status-chip" data-state="${escapeHtml(item.status)}">${escapeHtml(item.status)}</span><h2>${escapeHtml(item.title)}</h2><p>${escapeHtml(item.source_kind)} · ${escapeHtml(item.source_id)}</p></div><div class="project-score"><b>${formatBytes(item.size_bytes)}</b><small>${escapeHtml(item.storage_state)}</small></div></header>
      <div class="project-detail-meta"><span>類型 ${escapeHtml(item.artifact_type)}</span><span>Worker ${escapeHtml(item.worker || "未記錄")}</span><span>模型 ${escapeHtml(item.model || "未記錄")}</span><span>建立 ${formatTime(item.created_at)}</span></div>
      ${this.preview(item)}
      <section><div class="section-heading"><h3>完整性與來源</h3><span>${escapeHtml(item.integrity_status)}</span></div><div class="result-summary"><p>SHA-256<br><code>${escapeHtml(item.content_sha256)}</code></p><p>來源任務：${escapeHtml(item.task_id || "無")}</p><p>來源專案：${escapeHtml(item.project_id || "無")}</p></div></section>
      <section><div class="section-heading"><h3>真實使用技能</h3><span>${item.skills.length} 筆</span></div>${usages ? `<ul class="usage-list">${usages}</ul>` : '<div class="empty-state"><p>沒有實際 Worker 技能執行紀錄。</p></div>'}</section>
      <div class="dialog-actions"><a class="secondary-button result-download" href="/v1/workspace/results/${escapeHtml(item.artifact_id)}/preview">預覽／下載</a><button class="secondary-button" type="button" data-archive-artifact="${escapeHtml(item.artifact_id)}">封存至 NAS</button><button class="secondary-button" type="button" data-restore-artifact="${escapeHtml(item.artifact_id)}">還原快取</button><button class="danger-button" type="button" data-delete-artifact="${escapeHtml(item.artifact_id)}">徹底刪除</button></div>
    `;
    }
    card(item) {
        return `
      <article class="result-card" data-open-result="${escapeHtml(item.artifact_id)}">
        ${this.thumbnail(item)}
        <button type="button" aria-label="開啟 ${escapeHtml(item.title)}"></button>
        <div class="result-card-head"><span>${escapeHtml(item.artifact_type)}</span><small>${formatTime(item.created_at)}</small></div>
        <h3>${escapeHtml(item.title)}</h3>
        <p>${escapeHtml(item.source_kind)} · ${escapeHtml(item.worker || "unknown worker")}</p>
        <div class="result-card-meta"><span>${formatBytes(item.size_bytes)}</span><span>${escapeHtml(item.storage_state)}</span><span>${escapeHtml(item.integrity_status)}</span></div>
      </article>
    `;
    }
    thumbnail(item) {
        const url = `/v1/workspace/results/${encodeURIComponent(item.artifact_id)}/preview`;
        if (item.artifact_type === "image") {
            return `<img class="result-thumb" src="${url}" alt="" loading="lazy" decoding="async">`;
        }
        if (item.artifact_type === "video") {
            return `<video class="result-thumb" preload="none" muted playsinline data-src="${url}"></video>`;
        }
        return `<div class="result-placeholder"><span>${escapeHtml(item.artifact_type.slice(0, 3).toUpperCase())}</span></div>`;
    }
    preview(item) {
        const url = `/v1/workspace/results/${encodeURIComponent(item.artifact_id)}/preview`;
        if (item.artifact_type === "image") {
            return `<img class="result-preview" src="${url}" alt="${escapeHtml(item.title)}" loading="lazy">`;
        }
        if (item.artifact_type === "video") {
            return `<video class="result-preview" src="${url}" controls preload="metadata"></video>`;
        }
        return `<div class="result-file-preview"><span>${escapeHtml(item.artifact_type)}</span><p>${escapeHtml(concise(item.metadata, 600) || "檔案型成果")}</p></div>`;
    }
}
export class LibraryDomainComponent {
    render(data) {
        const target = document.querySelector("#library-domains");
        if (!target)
            return;
        target.innerHTML = data.items.map((domain) => {
            const report = domain.readiness;
            const passed = report?.passed === true;
            const book = domain.books[0];
            const gaps = report?.critical_gaps?.join(" · ") || "等待完整評估";
            return `
        <article class="domain-card" data-state="${escapeHtml(domain.status)}">
          <div class="domain-card-head"><span>${escapeHtml(domain.status.replaceAll("_", " "))}</span><small>${formatTime(domain.updated_at)}</small></div>
          <h3>${escapeHtml(domain.title)}</h3><p>${escapeHtml(domain.description || "尚無領域說明")}</p>
          <div class="domain-stats"><span><b>${domain.card_count}</b> 卡片</span><span><b>${Object.values(report?.gates ?? {}).filter(Boolean).length}</b> / ${Object.keys(report?.gates ?? {}).length || 5} 門檻</span><span class="${passed ? "passed" : ""}">${passed ? "可成書" : "未達門檻"}</span></div>
          <details><summary>準備度與重大缺口</summary><p>${escapeHtml(gaps)}</p><pre>${escapeHtml(concise(report?.metrics ?? domain.thresholds, 900))}</pre></details>
          <div class="domain-actions"><button type="button" class="secondary-button" data-evaluate-domain="${escapeHtml(domain.domain_id)}">重新評估</button>${passed && !book ? `<button type="button" class="primary-button" data-author-domain="${escapeHtml(domain.domain_id)}">建立正式書籍</button>` : ""}${book ? `<button type="button" class="secondary-button" data-open-book="${escapeHtml(book.book_id)}">閱讀 r${book.current_revision}</button>` : ""}</div>
          ${book ? `<form class="library-suggestion-form" data-book-id="${escapeHtml(book.book_id)}"><label>修改建議<textarea name="content" rows="3" minlength="3" maxlength="8000" placeholder="提出修改、補充章節或要求重新查證"></textarea></label><div><select name="suggestion_type"><option value="revision">修改建議</option><option value="question">詢問這本書</option><option value="chapter">補充章節</option><option value="reverify">重新查證</option></select><button class="secondary-button" type="submit">建立修訂任務</button></div></form>` : ""}
        </article>
      `;
        }).join("") || '<div class="empty-state wide"><p>尚未建立領域。知識目錄仍會持續累積，但不會自動冒充正式書籍。</p></div>';
    }
    renderBook(book) {
        const revisions = Array.isArray(book.revisions) ? book.revisions : [];
        const latest = revisions[0];
        const history = revisions.map((revision) => `
      <li><b>r${escapeHtml(revision.revision)}</b><span>${formatTime(String(revision.created_at ?? ""))}</span><small>${escapeHtml(revision.diff_summary)}</small><a href="/v1/workspace/library/books/${escapeHtml(book.book_id)}/revisions/${escapeHtml(revision.revision_id)}/download?format=markdown">Markdown</a><a href="/v1/workspace/library/books/${escapeHtml(book.book_id)}/revisions/${escapeHtml(revision.revision_id)}/download?format=json">JSON</a></li>
    `).join("");
        return `<header class="project-detail-head"><div><span class="status-chip" data-state="published">VERIFIED BOOK</span><h2>${escapeHtml(book.title)}</h2><p>${escapeHtml(book.description)}</p></div><div class="project-score"><b>r${escapeHtml(book.current_revision)}</b><small>${escapeHtml(book.status)}</small></div></header>${latest ? `<div class="result-summary"><p>最新內容雜湊<br><code>${escapeHtml(latest.content_sha256)}</code></p><p>${escapeHtml(latest.reason)}</p></div>` : ""}<section><div class="section-heading"><h3>修訂歷史</h3><span>${revisions.length} 版</span></div><ul class="revision-list">${history}</ul></section>`;
    }
}
export const renderArchiveStatus = (data) => {
    const target = document.querySelector("#archive-summary");
    if (!target)
        return;
    target.innerHTML = [
        ["NAS Provider", data.state],
        ["封存根目錄", data.root || "尚未設定"],
        ["本機快取", `${formatBytes(data.cache.size_bytes)} / ${formatBytes(data.cache.max_bytes)}`],
        ["快取項目", `${data.cache.entries} · 使用中 ${data.cache.in_use}`],
    ].map(([label, value]) => `<div><span>${escapeHtml(label)}</span><b>${escapeHtml(value)}</b></div>`).join("");
};
