// 单机数据包报告生成器前端
// 页面：型号数据库（database，主页面，逻辑在 db.js）+ 数据一致性对比（consistency）
console.log("单机数据包报告生成器前端加载");

// 初始化
document.addEventListener('DOMContentLoaded', function() {
    initConsistency();
    checkStatus();

    // 每30秒检查一次状态
    setInterval(checkStatus, 30000);
});

// 根据文件名返回报告的 MIME 类型
function getReportMimeType(filename) {
    const lower = (filename || '').toLowerCase();
    if (lower.endsWith('.xlsx') || lower.endsWith('.xls')) {
        return 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
    }
    return 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
}

// 将 base64 内容解码并触发浏览器下载
function downloadBase64AsFile(base64Content, filename) {
    const binaryString = atob(base64Content);
    const bytes = new Uint8Array(binaryString.length);
    for (let i = 0; i < binaryString.length; i++) {
        bytes[i] = binaryString.charCodeAt(i);
    }

    const blob = new Blob([bytes], { type: getReportMimeType(filename) });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;

    document.body.appendChild(a);
    a.click();

    window.URL.revokeObjectURL(url);
    document.body.removeChild(a);
}

// 检查后端状态
async function checkStatus() {
    try {
        const response = await fetch('/api/health');
        const data = await response.json();
        
        if (data.status === 'ok') {
            document.getElementById('backendStatus').textContent = '运行正常';
            document.getElementById('backendStatus').className = 'status status-ok';
        } else {
            document.getElementById('backendStatus').textContent = '异常';
            document.getElementById('backendStatus').className = 'status status-error';
        }
    } catch (error) {
        document.getElementById('backendStatus').textContent = '连接失败';
        document.getElementById('backendStatus').className = 'status status-error';
    }
}

// 显示消息
function showMessage(text, type = 'info') {
    // 创建消息元素
    const message = document.createElement('div');
    message.className = `message message-${type}`;
    message.textContent = text;
    document.body.appendChild(message);
    
    // 3秒后自动移除
    setTimeout(() => {
        if (message.parentNode) {
            message.parentNode.removeChild(message);
        }
    }, 3000);
}

// ============ 数据一致性对比 ============
let consistencyModel = '';
let consistencyBatches = [];
let consistencyParams = [];
let consistencySelectedBatches = new Set();
let consistencySelectedParamIds = new Set();

function showView(name) {
    // 视图切换：model（型号详情） / consistency（数据一致性对比） / empty（未选择）
    document.getElementById('modelView').style.display = name === 'model' ? '' : 'none';
    document.getElementById('consistencyView').style.display = name === 'consistency' ? '' : 'none';
    document.getElementById('emptyView').style.display = name === 'empty' ? '' : 'none';
}

function initConsistency() {
    const select = document.getElementById('consistencyModelSelect');
    select.addEventListener('change', () => {
        consistencyModel = select.value;
        if (consistencyModel) {
            loadConsistencyParameters(consistencyModel);
        } else {
            resetConsistencyState();
        }
    });

    document.getElementById('consistencyImportFile').addEventListener('change', importConsistencyConfig);
}

function resetConsistencyState() {
    consistencyBatches = [];
    consistencyParams = [];
    consistencySelectedBatches = new Set();
    consistencySelectedParamIds = new Set();
    document.getElementById('consistencyBatchSection').style.display = 'none';
    document.getElementById('consistencyActionSection').style.display = 'none';
    document.getElementById('consistencyActionHint').style.display = 'block';
    document.getElementById('consistencyParamToolbar').style.display = 'none';
    document.getElementById('consistencyParamTableWrap').innerHTML =
        '<div class="empty-state"><div class="empty-icon">📋</div><p>请先选择型号</p></div>';
    document.getElementById('consistencySelectedCount').textContent = '';
    updateConsistencyButtons();
}

async function loadConsistencyParameters(model) {
    const tableWrap = document.getElementById('consistencyParamTableWrap');
    tableWrap.innerHTML = '<div class="empty-state"><p>加载参数中...</p></div>';
    try {
        const response = await fetch('/api/consistency/parameters', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model: model })
        });
        const data = await response.json();

        if (!data.success) {
            showMessage(data.error || '读取参数失败', 'error');
            tableWrap.innerHTML = '<div class="empty-state"><p>读取参数失败</p></div>';
            return;
        }

        consistencyBatches = data.batch_numbers || [];
        consistencyParams = data.parameters || [];
        consistencySelectedBatches = new Set(consistencyBatches);
        consistencySelectedParamIds = new Set();

        document.getElementById('consistencyBatchSection').style.display = 'block';
        document.getElementById('consistencyActionSection').style.display = 'flex';
        document.getElementById('consistencyActionHint').style.display = 'none';
        document.getElementById('consistencyParamToolbar').style.display = 'flex';

        renderConsistencyBatches();
        renderConsistencyParams();
        updateConsistencyButtons();
    } catch (error) {
        showMessage('读取参数时出错: ' + error.message, 'error');
        tableWrap.innerHTML = '<div class="empty-state"><p>读取参数失败</p></div>';
    }
}

function renderConsistencyBatches() {
    const list = document.getElementById('consistencyBatchList');
    list.innerHTML = '';

    consistencyBatches.forEach((batch) => {
        const label = document.createElement('label');
        label.className = 'checkbox-item';

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = consistencySelectedBatches.has(batch);
        checkbox.onchange = () => {
            if (checkbox.checked) {
                consistencySelectedBatches.add(batch);
            } else {
                consistencySelectedBatches.delete(batch);
            }
            updateConsistencyButtons();
        };

        label.appendChild(checkbox);
        label.appendChild(document.createTextNode(batch));
        list.appendChild(label);
    });
}

function renderConsistencyParams() {
    const wrap = document.getElementById('consistencyParamTableWrap');
    const params = consistencyParams;

    if (!params.length) {
        wrap.innerHTML = '<div class="empty-state"><p>未找到匹配的参数</p></div>';
        updateConsistencySelectedCount();
        return;
    }

    let html = '<table class="data-table"><thead><tr>' +
        '<th style="width:40px"></th><th>参数ID</th><th>Description</th>' +
        '</tr></thead><tbody>';

    params.forEach((param) => {
        const checked = consistencySelectedParamIds.has(param.id) ? 'checked' : '';
        html += `<tr><td><input type="checkbox" data-param-id="${escapeHtml(param.id)}" ${checked}></td>` +
                `<td>${escapeHtml(param.id)}</td>` +
                `<td>${escapeHtml(param.description)}</td></tr>`;
    });
    html += '</tbody></table>';
    wrap.innerHTML = html;

    wrap.querySelectorAll('input[type="checkbox"]').forEach((checkbox) => {
        checkbox.addEventListener('change', () => {
            const id = checkbox.dataset.paramId;
            if (checkbox.checked) {
                consistencySelectedParamIds.add(id);
            } else {
                consistencySelectedParamIds.delete(id);
            }
            updateConsistencyButtons();
            updateConsistencySelectedCount();
        });
    });

    updateConsistencySelectedCount();
}

function toggleAllConsistencyParams(checked) {
    consistencyParams.forEach((param) => {
        if (checked) {
            consistencySelectedParamIds.add(param.id);
        } else {
            consistencySelectedParamIds.delete(param.id);
        }
    });
    renderConsistencyParams();
    updateConsistencyButtons();
}

function updateConsistencySelectedCount() {
    document.getElementById('consistencySelectedCount').textContent =
        `已选 ${consistencySelectedParamIds.size} 个参数`;
}

function updateConsistencyButtons() {
    const exportBtn = document.getElementById('consistencyExportWordBtn');
    exportBtn.disabled =
        consistencySelectedBatches.size === 0 || consistencySelectedParamIds.size === 0;
}

function escapeHtml(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function exportConsistencyConfig() {
    const config = {
        batch_numbers: [...consistencySelectedBatches],
        parameter_ids: [...consistencySelectedParamIds]
    };
    const blob = new Blob([JSON.stringify(config, null, 2)], {
        type: 'application/json'
    });
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `一致性对比配置_${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(url);
    showMessage('配置已导出', 'success');
}

function importConsistencyConfig(event) {
    const file = event.target.files[0];
    event.target.value = '';
    if (!file) {
        return;
    }

    const reader = new FileReader();
    reader.onload = () => {
        try {
            const config = JSON.parse(reader.result);
            const batches = Array.isArray(config.batch_numbers) ? config.batch_numbers : [];
            const ids = Array.isArray(config.parameter_ids) ? config.parameter_ids : [];
            const validBatches = new Set(consistencyBatches);
            const validIds = new Set(consistencyParams.map((param) => param.id));

            consistencySelectedBatches = new Set(
                batches.filter((batch) => validBatches.has(batch))
            );
            consistencySelectedParamIds = new Set(
                ids.filter((id) => validIds.has(id))
            );

            renderConsistencyBatches();
            renderConsistencyParams();
            updateConsistencyButtons();
            showMessage('配置已导入', 'success');
        } catch (e) {
            showMessage('配置文件格式不正确', 'error');
        }
    };
    reader.readAsText(file);
}

async function exportConsistencyWord() {
    if (!consistencyModel) {
        showMessage('请先选择型号', 'warning');
        return;
    }
    if (consistencySelectedBatches.size === 0 || consistencySelectedParamIds.size === 0) {
        showMessage('请先勾选批次和参数', 'warning');
        return;
    }

    const btn = document.getElementById('consistencyExportWordBtn');
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = '生成中...';

    try {
        const response = await fetch('/api/consistency/compare', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                model: consistencyModel,
                batch_numbers: [...consistencySelectedBatches],
                parameter_ids: [...consistencySelectedParamIds]
            })
        });
        const data = await response.json();

        if (data.success) {
            downloadBase64AsFile(data.report_content, data.output_filename);
            const stats = data.statistics || {};
            showMessage(
                `对比完成: ${stats.parameter_count} 个参数, ${stats.inconsistent_count} 项不一致`,
                'success'
            );
        } else {
            showMessage(data.error || '生成失败', 'error');
        }
    } catch (error) {
        showMessage('生成一致性报告时出错: ' + error.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = originalText;
        updateConsistencyButtons();
    }
}
