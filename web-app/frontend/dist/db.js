// 型号数据库页逻辑（左侧导航 / 型号详情 / 模板导出 / 一键生成 / 内容预览 / 版本管理）
// 依赖 app.js 中的公共工具：escapeHtml / showMessage / downloadBase64AsFile / showView
// 列表项统一用 data-* + 事件委托，避免文件名/型号名破坏内联 onclick
const DatabasePage = (() => {
    'use strict';

    let overview = [];
    let currentModel = null;
    let detail = null;
    let selectedBatch = '';
    let generating = false;      // 单模板导出进行中
    let generatingAll = false;   // 一键生成进行中
    let previewBatches = [];
    let previewParams = [];
    let previewChecked = new Set();   // 预览表中显示的批次列
    let previewModel = null;          // 预览数据所属型号（切换型号时重置勾选）
    let cellEdits = new Map();        // 未保存的单元格编辑: key -> value

    // ---------- 基础工具 ----------
    async function fetchJson(url, options) {
        const response = await fetch(url, options);
        let data = null;
        try {
            data = await response.json();
        } catch (e) {
            throw new Error(`服务端响应异常（HTTP ${response.status}）`);
        }
        if (!response.ok || (!data.success && data.error)) {
            throw new Error(data.error || `请求失败（HTTP ${response.status}）`);
        }
        return data;
    }

    function fmtSize(bytes) {
        if (bytes == null) return '-';
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
        return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
    }

    function $(id) {
        return document.getElementById(id);
    }

    function setBusy(btn, busy, busyText) {
        if (!btn) return;
        if (busy) {
            btn.dataset.originalText = btn.textContent;
            btn.disabled = true;
            if (busyText) btn.textContent = busyText;
        } else {
            btn.disabled = false;
            if (btn.dataset.originalText) btn.textContent = btn.dataset.originalText;
        }
    }

    // ---------- 侧边栏 ----------
    function renderSidebar() {
        const list = $('sidebarModelList');
        if (!list) return;
        if (!overview.length) {
            list.innerHTML = '<div class="sidebar-empty">暂无型号</div>';
            return;
        }
        list.innerHTML = overview.map((m) => `
            <button class="side-item${m.model === currentModel ? ' active' : ''}" data-model="${escapeHtml(m.model)}">
                <span class="model-dot${m.has_data ? ' ok' : ''}"></span>
                <span class="model-name">${escapeHtml(m.model)}</span>
            </button>`).join('');
    }

    function setActiveSidebar(modelName) {
        document.querySelectorAll('#sidebarModelList .side-item').forEach((el) => {
            el.classList.toggle('active', el.dataset.model === modelName);
        });
        const consItem = $('navConsistencyItem');
        if (consItem) consItem.classList.toggle('active', modelName === null && viewName === 'consistency');
    }

    let viewName = 'empty';

    function selectModel(model) {
        currentModel = model;
        viewName = 'model';
        showView('model');
        setActiveSidebar(model);
        $('modelTitle').textContent = model;
        resetModelSelection();
        loadDetail();
    }

    function selectConsistency() {
        viewName = 'consistency';
        showView('consistency');
        setActiveSidebar(null);
    }

    function resetModelSelection() {
        selectedBatch = '';
        previewBatches = [];
        previewParams = [];
        previewChecked = new Set();
        previewModel = null;
        cellEdits = new Map();
        updateSaveState();
    }

    // ---------- 总览 ----------
    async function loadOverview() {
        try {
            const data = await fetchJson('/api/database/overview');
            overview = data.models || [];
        } catch (error) {
            overview = [];
            showMessage('加载数据库失败: ' + error.message, 'error');
        }
        renderSidebar();
        renderConsistencyModelOptions();
        // 当前打开的型号若已不存在，回到空视图
        if (currentModel && !overview.some((m) => m.model === currentModel)) {
            currentModel = null;
            viewName = 'empty';
            showView('empty');
        }
    }

    function refresh() {
        return loadOverview();
    }

    // ---------- 型号详情 ----------
    async function loadDetail() {
        const panels = ['dbDataFileInfo', 'dbTemplateList', 'dbVersionList', 'dbPreviewWrap'];
        panels.forEach((id) => {
            const el = $(id);
            if (el) el.innerHTML = '<div class="loading-panel"><span class="loading"></span>加载中...</div>';
        });
        const select = $('dbBatchSelect');
        if (select) {
            select.innerHTML = '<option value="">加载中...</option>';
            select.disabled = true;
        }
        try {
            detail = await fetchJson(`/api/database/models/${encodeURIComponent(currentModel)}`);
            renderDetail();
            loadPreview();
        } catch (error) {
            panels.forEach((id) => {
                const el = $(id);
                if (el) el.innerHTML = `<div class="empty-state">❌ ${escapeHtml(error.message)}</div>`;
            });
        }
    }

    function renderDetail() {
        // 解析错误提示
        const alertEl = $('dbParseError');
        if (detail.parse_error) {
            alertEl.textContent = `⚠ 数据包解析失败: ${detail.parse_error}`;
            alertEl.style.display = 'block';
        } else {
            alertEl.style.display = 'none';
        }

        // 数据包信息
        const infoEl = $('dbDataFileInfo');
        if (detail.has_data && detail.data_file) {
            const stats = detail.document_stats || {};
            infoEl.innerHTML = `
                <div class="info-line"><span class="info-key">文件名</span><span class="info-val">${escapeHtml(detail.data_file.name)}</span></div>
                <div class="info-line"><span class="info-key">大小</span><span class="info-val">${fmtSize(detail.data_file.size)}</span></div>
                <div class="info-line"><span class="info-key">更新时间</span><span class="info-val">${escapeHtml(detail.data_file.mtime)}</span></div>
                <div class="info-line"><span class="info-key">参数数</span><span class="info-val">${detail.parameter_count}</span></div>
                <div class="info-line"><span class="info-key">通用文档</span><span class="info-val">${stats['通用文档'] || 0}</span></div>
                <div class="info-line"><span class="info-key">单机文档</span><span class="info-val">${stats['单机文档'] || 0}</span></div>
            `;
        } else {
            infoEl.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">📭</div>
                    <p>该型号还没有数据包</p>
                    <p style="margin-top:8px">点击右上角「⬆ 上传新数据包」导入 .xlsx</p>
                </div>`;
        }

        // 批次下拉框
        const select = $('dbBatchSelect');
        if (detail.batch_numbers && detail.batch_numbers.length) {
            select.disabled = false;
            select.innerHTML = '<option value="">请选择批次号</option>' + detail.batch_numbers.map((b) =>
                `<option value="${escapeHtml(b)}"${b === selectedBatch ? ' selected' : ''}>${escapeHtml(b)}</option>`
            ).join('');
        } else {
            select.disabled = true;
            select.innerHTML = '<option value="">无批次数据</option>';
        }

        // 模板列表（每个模板右侧带导出按钮，显示配置的报告类型）
        const tplEl = $('dbTemplateList');
        if (detail.templates && detail.templates.length) {
            tplEl.innerHTML = detail.templates.map((t) => `
                <div class="template-item">
                    <span class="template-icon">${t.name.toLowerCase().endsWith('.xlsx') ? '📊' : '📝'}</span>
                    <span class="template-name">
                        ${escapeHtml(t.name)}
                        <span class="tpl-badge${t.report_type ? '' : ' unset'}">${escapeHtml(t.report_type || '未标注类型')}</span>
                    </span>
                    <span class="template-meta">${fmtSize(t.size)} · ${escapeHtml(t.mtime)}</span>
                    <button class="btn btn-mini btn-success" data-action="export" data-template="${escapeHtml(t.name)}" disabled>⬇ 导出</button>
                </div>`).join('');
        } else {
            tplEl.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">📄</div>
                    <p>暂无模板，点击右上角「📝 上传模板」</p>
                </div>`;
        }

        renderVersions();
        updateGenerateState();
    }

    function renderVersions() {
        const el = $('dbVersionList');
        if (!detail.versions || !detail.versions.length) {
            el.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">🕘</div>
                    <p>暂无历史版本，上传新数据包后自动记录</p>
                </div>`;
            return;
        }
        const sourceLabel = { auto: '自动快照', upload: '上传', edit: '表格编辑' };
        el.innerHTML = detail.versions.map((v) => {
            const sub = [
                `${fmtSize(v.size)} · ${sourceLabel[v.source] || v.source || '快照'}`,
                v.note ? `<span class="version-note">“${escapeHtml(v.note)}”</span>` : ''
            ].filter(Boolean).join(' · ');
            return `
                <div class="version-item">
                    <span class="version-dot"></span>
                    <div class="version-main">
                        <div class="version-time">${escapeHtml(v.time)}</div>
                        <div class="version-sub">${sub}</div>
                    </div>
                    <div class="version-actions">
                        <button class="btn btn-mini" data-action="download" data-vid="${escapeHtml(v.id)}">⬇ 下载</button>
                        <button class="btn btn-mini" data-action="restore" data-vid="${escapeHtml(v.id)}">↺ 恢复</button>
                        <button class="btn btn-mini btn-danger" data-action="delete" data-vid="${escapeHtml(v.id)}">删除</button>
                    </div>
                </div>`;
        }).join('');
    }

    // ---------- 数据内容预览（批次勾选 + 可编辑） ----------
    async function loadPreview() {
        const wrap = $('dbPreviewWrap');
        try {
            const data = await fetchJson(`/api/database/models/${encodeURIComponent(currentModel)}/parameters`);
            previewBatches = data.batch_numbers || [];
            previewParams = data.parameters || [];

            // 切换型号时重置勾选；同一型号保持用户勾选
            if (previewModel !== currentModel) {
                previewChecked = new Set(previewBatches);
                previewModel = currentModel;
                cellEdits = new Map();
            }
            // 勾选中已不存在的批次列（如数据包更新后批次变化）
            previewChecked = new Set([...previewChecked].filter((b) => previewBatches.includes(b)));

            renderBatchChecks();
            renderPreviewTable();
            updateSaveState();
        } catch (error) {
            wrap.innerHTML = `<div class="empty-state">❌ ${escapeHtml(error.message)}</div>`;
            $('dbBatchChecks').innerHTML = '';
        }
    }

    function renderBatchChecks() {
        const box = $('dbBatchChecks');
        if (!previewBatches.length) {
            box.innerHTML = '';
            return;
        }
        box.innerHTML = '<span class="batch-check-label">显示批次列:</span>' + previewBatches.map((b) => `
            <label class="checkbox-item">
                <input type="checkbox" data-batch="${escapeHtml(b)}"${previewChecked.has(b) ? ' checked' : ''}>
                <span>${escapeHtml(b)}</span>
            </label>`).join('');
    }

    function togglePreviewBatch(batch, checked) {
        if (checked) {
            previewChecked.add(batch);
        } else {
            previewChecked.delete(batch);
        }
        renderPreviewTable();
    }

    function editKey(paramId, field, batch) {
        return field === 'value' ? `${paramId}|value|${batch}` : `${paramId}|description`;
    }

    function renderPreviewTable() {
        const wrap = $('dbPreviewWrap');
        if (!previewParams.length) {
            wrap.innerHTML = '<div class="empty-state"><p>无参数数据</p></div>';
            return;
        }
        const keyword = ($('dbPreviewSearch').value || '').trim().toLowerCase();
        const rows = previewParams.filter((p) =>
            !keyword ||
            p.id.toLowerCase().includes(keyword) ||
            (p.description || '').toLowerCase().includes(keyword)
        );
        const cols = previewBatches.filter((b) => previewChecked.has(b));

        const head = '<tr>' +
            '<th class="mono">参数ID</th><th>Description</th>' +
            cols.map((b) => `<th class="mono">${escapeHtml(b)}</th>`).join('') +
            '</tr>';
        const body = rows.map((p) => {
            let html = `<tr><td class="mono">${escapeHtml(p.id)}</td>`;
            const desc = cellEdits.get(editKey(p.id, 'description')) ?? (p.description || '');
            html += `<td class="editable${cellEdits.has(editKey(p.id, 'description')) ? ' dirty' : ''}"` +
                ` contenteditable="true" data-param="${escapeHtml(p.id)}" data-field="description">${escapeHtml(desc)}</td>`;
            for (const b of cols) {
                const key = editKey(p.id, 'value', b);
                const val = cellEdits.get(key) ?? ((p.values || {})[b] || '');
                html += `<td class="editable${cellEdits.has(key) ? ' dirty' : ''}"` +
                    ` contenteditable="true" data-param="${escapeHtml(p.id)}" data-batch="${escapeHtml(b)}">${escapeHtml(val)}</td>`;
            }
            return html + '</tr>';
        }).join('');

        wrap.innerHTML = rows.length
            ? `<table class="data-table"><thead>${head}</thead><tbody>${body}</tbody></table>`
            : '<div class="empty-state"><p>没有匹配的参数</p></div>';
    }

    function filterPreview() {
        renderPreviewTable();
    }

    // ---------- 编辑保存 ----------
    function updateSaveState() {
        const saveBtn = $('dbSaveEditsBtn');
        const discardBtn = $('dbDiscardBtn');
        const count = $('dbDirtyCount');
        if (!saveBtn) return;
        saveBtn.disabled = cellEdits.size === 0 || !currentModel;
        discardBtn.disabled = cellEdits.size === 0;
        count.textContent = cellEdits.size ? `已修改 ${cellEdits.size} 处` : '';
    }

    async function saveEdits() {
        if (!currentModel || cellEdits.size === 0) {
            return;
        }
        const edits = [];
        for (const [key, value] of cellEdits) {
            const [paramId, field, batch] = key.split('|');
            edits.push({ param_id: paramId, field, batch: batch || '', value });
        }
        const btn = $('dbSaveEditsBtn');
        setBusy(btn, true, '保存中...');
        try {
            const data = await fetchJson(`/api/database/models/${encodeURIComponent(currentModel)}/data/update`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ edits })
            });
            cellEdits = new Map();
            showMessage(`已写回 ${data.updated} 处修改，旧版本已自动快照`, 'success');
            if (data.missing && data.missing.length) {
                showMessage(`有 ${data.missing.length} 处未定位到数据包单元格: ${data.missing.join('、')}`, 'warning');
            }
            await Promise.all([loadPreview(), loadDetail()]);
        } catch (error) {
            showMessage('保存失败: ' + error.message, 'error');
        } finally {
            setBusy(btn, false);
            updateSaveState();
        }
    }

    function discardEdits() {
        cellEdits = new Map();
        renderPreviewTable();
        updateSaveState();
        showMessage('已放弃未保存的修改', 'info');
    }

    // ---------- 模板报告类型配置 ----------
    function openTplConfigModal() {
        if (!detail || !detail.templates || !detail.templates.length) {
            showMessage('该型号暂无模板', 'warning');
            return;
        }
        const rows = detail.templates.map((t) => {
            const stem = t.name.replace(/\.(docx|xlsx)$/i, '');
            const value = t.report_type || '';
            return `
                <label class="field tpl-config-field">
                    <span class="tpl-config-name">${t.name.toLowerCase().endsWith('.xlsx') ? '📊' : '📝'} ${escapeHtml(t.name)}</span>
                    <input type="text" data-stem="${escapeHtml(stem)}" data-name="${escapeHtml(t.name)}" placeholder="报告名称，如：测试报告" value="${escapeHtml(value)}">
                </label>`;
        }).join('');
        $('dbTplConfigRows').innerHTML = rows;
        openModal('dbTplConfigModal');
    }

    async function saveTplConfig() {
        const inputs = [...document.querySelectorAll('#dbTplConfigRows input')];
        const types = {};
        for (const input of inputs) {
            const v = input.value.trim();
            if (v) {
                types[input.dataset.stem] = v;
                types[input.dataset.name] = v;
            }
        }
        const btn = $('dbTplConfigSaveBtn');
        setBusy(btn, true, '保存中...');
        try {
            await fetchJson(`/api/database/models/${encodeURIComponent(currentModel)}/templates/config`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ types })
            });
            closeModal('dbTplConfigModal');
            showMessage('模板报告类型已保存到 templates.json', 'success');
            await loadDetail();
        } catch (error) {
            showMessage('保存失败: ' + error.message, 'error');
        } finally {
            setBusy(btn, false);
        }
    }

    // ---------- 批次选择与生成 ----------
    function selectBatch(batch) {
        selectedBatch = batch || '';
        updateGenerateState();
    }

    function updateGenerateState() {
        const canExport = !!(currentModel && detail && detail.has_data && selectedBatch && !generating);
        // 每个模板行的导出按钮
        document.querySelectorAll('#dbTemplateList button[data-action="export"]').forEach((btn) => {
            btn.disabled = !canExport;
        });
        const allBtn = $('dbGenerateAllBtn');
        if (allBtn) allBtn.disabled = !canExport || generatingAll || !(detail && detail.templates && detail.templates.length);
        const hint = $('dbGenerateHint');
        if (hint) {
            hint.textContent = generating
                ? '正在导出报告...'
                : generatingAll
                    ? '正在生成全部报告...'
                    : selectedBatch
                        ? `批次 ${selectedBatch}：可导出单个模板或一键生成所有报告`
                        : '选择批次号后可导出报告';
        }
    }

    // 单模板导出：按当前选中批次生成并下载
    async function generateReport(templateName) {
        if (!currentModel || !selectedBatch || generating) {
            if (!selectedBatch) showMessage('请先选择批次号', 'warning');
            return;
        }
        generating = true;
        updateGenerateState();
        const btn = document.querySelector(`#dbTemplateList button[data-template="${CSS.escape(templateName)}"]`);
        setBusy(btn, true, '导出中...');
        try {
            const data = await fetchJson(`/api/database/models/${encodeURIComponent(currentModel)}/generate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    template_filename: templateName,
                    batch_number: selectedBatch
                })
            });
            downloadBase64AsFile(data.report_content, data.output_filename);
            const stats = data.statistics || {};
            showMessage(
                `报告生成成功: ${stats.parameter_replacements || 0} 参数 / ${stats.document_replacements || 0} 文档替换`,
                'success'
            );
        } catch (error) {
            showMessage(`导出失败: ${error.message}`, 'error');
        } finally {
            generating = false;
            setBusy(btn, false);
            updateGenerateState();
        }
    }

    // 一键生成所有模板报告（zip 下载）
    async function generateAll() {
        if (!currentModel || !selectedBatch || generatingAll) {
            if (!selectedBatch) showMessage('请先选择批次号', 'warning');
            return;
        }
        generatingAll = true;
        const btn = $('dbGenerateAllBtn');
        setBusy(btn, true, '⏳ 生成中...');
        updateGenerateState();
        try {
            const data = await fetchJson(`/api/database/models/${encodeURIComponent(currentModel)}/generate_all`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ batch_number: selectedBatch })
            });
            downloadBase64AsFile(data.report_content, data.zip_filename);
            const stats = data.statistics || {};
            const failed = (data.results || []).filter((r) => !r.success);
            showMessage(
                `一键生成完成: 成功 ${stats.success_count}/${stats.total_templates} 个，已打包下载` +
                (failed.length ? `；失败: ${failed.map((f) => f.template).join('、')}` : ''),
                failed.length ? 'warning' : 'success'
            );
        } catch (error) {
            showMessage('一键生成失败: ' + error.message, 'error');
        } finally {
            generatingAll = false;
            setBusy(btn, false);
            updateGenerateState();
        }
    }

    // ---------- 新建型号 ----------
    function openCreateModal() {
        $('dbNewModelName').value = '';
        $('dbNewModelFile').value = '';
        openModal('dbCreateModal');
    }

    async function submitCreate() {
        const name = $('dbNewModelName').value.trim();
        const fileInput = $('dbNewModelFile');
        if (!name) {
            showMessage('请输入型号名称', 'warning');
            return;
        }
        const btn = $('dbCreateConfirmBtn');
        setBusy(btn, true, '创建中...');
        try {
            const formData = new FormData();
            formData.append('name', name);
            if (fileInput.files.length > 0) {
                formData.append('file', fileInput.files[0]);
            }
            await fetchJson('/api/database/models', { method: 'POST', body: formData });
            closeModal('dbCreateModal');
            showMessage(`型号 "${name}" 创建成功`, 'success');
            await loadOverview();
            selectModel(name);
        } catch (error) {
            showMessage('创建失败: ' + error.message, 'error');
        } finally {
            setBusy(btn, false);
        }
    }

    // ---------- 上传数据包 ----------
    function openUploadDataModal() {
        if (!currentModel) return;
        $('dbUploadDataFile').value = '';
        $('dbUploadDataNote').value = '';
        openModal('dbUploadDataModal');
    }

    async function submitUploadData() {
        const fileInput = $('dbUploadDataFile');
        if (!fileInput.files.length) {
            showMessage('请选择 .xlsx 数据包文件', 'warning');
            return;
        }
        const btn = $('dbUploadDataConfirmBtn');
        setBusy(btn, true, '上传中...');
        try {
            const formData = new FormData();
            formData.append('file', fileInput.files[0]);
            formData.append('note', $('dbUploadDataNote').value.trim());
            await fetchJson(`/api/database/models/${encodeURIComponent(currentModel)}/data`, {
                method: 'POST',
                body: formData
            });
            closeModal('dbUploadDataModal');
            showMessage('数据包已更新，旧版本已自动快照', 'success');
            await Promise.all([loadDetail(), loadOverview()]);
        } catch (error) {
            showMessage('上传失败: ' + error.message, 'error');
        } finally {
            setBusy(btn, false);
        }
    }

    // ---------- 上传模板 ----------
    function openUploadTemplatesModal() {
        if (!currentModel) return;
        $('dbUploadTemplateFiles').value = '';
        openModal('dbUploadTemplatesModal');
    }

    async function submitUploadTemplates() {
        const fileInput = $('dbUploadTemplateFiles');
        if (!fileInput.files.length) {
            showMessage('请选择模板文件', 'warning');
            return;
        }
        const btn = $('dbUploadTemplatesConfirmBtn');
        setBusy(btn, true, '上传中...');
        let ok = 0;
        let fail = 0;
        try {
            for (const file of fileInput.files) {
                const formData = new FormData();
                formData.append('file', file);
                try {
                    await fetchJson(`/api/database/models/${encodeURIComponent(currentModel)}/templates`, {
                        method: 'POST',
                        body: formData
                    });
                    ok += 1;
                } catch (error) {
                    fail += 1;
                    showMessage(`${file.name}: ${error.message}`, 'error');
                }
            }
            closeModal('dbUploadTemplatesModal');
            if (ok > 0) {
                showMessage(`模板上传完成（成功 ${ok} 个${fail ? `，失败 ${fail} 个` : ''}）`, 'success');
                await loadDetail();
            }
        } finally {
            setBusy(btn, false);
        }
    }

    // ---------- 版本操作 ----------
    async function downloadVersion(vid) {
        if (!currentModel) return;
        const url = `/api/database/models/${encodeURIComponent(currentModel)}/versions/${encodeURIComponent(vid)}/download`;
        const a = document.createElement('a');
        a.href = url;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
    }

    async function restoreVersion(vid) {
        if (!currentModel) return;
        if (!confirm(`确定将版本 ${vid} 恢复为当前数据包吗？\n当前数据包会先自动快照到版本历史。`)) {
            return;
        }
        try {
            await fetchJson(`/api/database/models/${encodeURIComponent(currentModel)}/versions/${encodeURIComponent(vid)}/restore`, {
                method: 'POST'
            });
            showMessage(`已恢复版本 ${vid}`, 'success');
            await Promise.all([loadDetail(), loadOverview()]);
        } catch (error) {
            showMessage('恢复失败: ' + error.message, 'error');
        }
    }

    async function deleteVersion(vid) {
        if (!currentModel) return;
        if (!confirm(`确定删除版本 ${vid} 吗？删除后不可恢复。`)) {
            return;
        }
        try {
            await fetchJson(`/api/database/models/${encodeURIComponent(currentModel)}/versions/${encodeURIComponent(vid)}`, {
                method: 'DELETE'
            });
            showMessage(`已删除版本 ${vid}`, 'success');
            await loadDetail();
        } catch (error) {
            showMessage('删除失败: ' + error.message, 'error');
        }
    }

    // ---------- 弹窗 ----------
    function openModal(id) {
        $(id).style.display = 'flex';
    }

    function closeModal(id) {
        $(id).style.display = 'none';
    }

    // ---------- 一致性页联动 ----------
    function renderConsistencyModelOptions() {
        const select = $('consistencyModelSelect');
        if (!select) return;
        const previous = select.value;
        select.innerHTML = '<option value="">请选择型号</option>' + overview.map((m) => {
            const name = escapeHtml(m.model);
            const suffix = m.has_data ? '' : '（无数据包）';
            return `<option value="${name}">${name}${suffix}</option>`;
        }).join('');
        if (previous && overview.some((m) => m.model === previous)) {
            select.value = previous;
        }
    }

    // ---------- 事件委托 ----------
    function initDelegatedEvents() {
        // 侧边栏型号
        $('sidebarModelList').addEventListener('click', (e) => {
            const item = e.target.closest('.side-item');
            if (item && item.dataset.model) {
                selectModel(item.dataset.model);
            }
        });

        // 模板列表按钮（导出）
        $('dbTemplateList').addEventListener('click', (e) => {
            const btn = e.target.closest('button[data-action="export"]');
            if (btn && btn.dataset.template) {
                generateReport(btn.dataset.template);
            }
        });

        // 版本操作按钮
        $('dbVersionList').addEventListener('click', (e) => {
            const btn = e.target.closest('button[data-action]');
            if (!btn) return;
            const vid = btn.dataset.vid;
            if (btn.dataset.action === 'download') downloadVersion(vid);
            else if (btn.dataset.action === 'restore') restoreVersion(vid);
            else if (btn.dataset.action === 'delete') deleteVersion(vid);
        });

        // 预览表批次勾选
        $('dbBatchChecks').addEventListener('change', (e) => {
            const checkbox = e.target.closest('input[type="checkbox"][data-batch]');
            if (checkbox) {
                togglePreviewBatch(checkbox.dataset.batch, checkbox.checked);
            }
        });

        // 预览表可编辑单元格：输入记录、粘贴纯文本
        const previewWrap = $('dbPreviewWrap');
        previewWrap.addEventListener('input', (e) => {
            const cell = e.target.closest('td.editable');
            if (!cell || !cell.dataset.param) return;
            const key = editKey(cell.dataset.param, cell.dataset.field || 'value', cell.dataset.batch);
            cellEdits.set(key, cell.innerText.replace(/\r/g, ''));
            cell.classList.add('dirty');
            updateSaveState();
        });
        previewWrap.addEventListener('paste', (e) => {
            const cell = e.target.closest('td.editable');
            if (!cell) return;
            e.preventDefault();
            const text = (e.clipboardData || window.clipboardData).getData('text/plain');
            document.execCommand('insertText', false, text);
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        initDelegatedEvents();
        loadOverview();
    });

    return {
        refresh,
        selectModel,
        selectConsistency,
        selectBatch,
        filterPreview,
        discardEdits,
        saveEdits,
        openTplConfigModal,
        saveTplConfig,
        generateReport,
        generateAll,
        openCreateModal,
        submitCreate,
        closeModal,
        openUploadDataModal,
        submitUploadData,
        openUploadTemplatesModal,
        submitUploadTemplates,
        downloadVersion,
        restoreVersion,
        deleteVersion
    };
})();

window.DatabasePage = DatabasePage;
