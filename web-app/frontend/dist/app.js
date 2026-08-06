// 单机数据包报告生成器前端（内存版）
console.log("单机数据包报告生成器前端加载（内存版）");

// 全局状态
let currentExcelFile = null;
let currentTemplateFile = null;
let currentBatch = '';
let currentBatches = [];

// 初始化
document.addEventListener('DOMContentLoaded', function() {
    initUpload();
    initConsistency();
    checkStatus();
    
    // 每30秒检查一次状态
    setInterval(checkStatus, 30000);
});

// 初始化上传功能
function initUpload() {
    // Excel 文件上传
    const excelUploadArea = document.getElementById('excelUploadArea');
    const excelFile = document.getElementById('excelFile');
    
    excelUploadArea.addEventListener('click', () => excelFile.click());
    setupDragDrop(excelUploadArea, excelFile, 'excel');
    
    excelFile.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            handleFileUpload(e.target.files[0], 'excel');
        }
    });
    
    // Word 文件上传
    const templateUploadArea = document.getElementById('templateUploadArea');
    const templateFile = document.getElementById('templateFile');
    
    templateUploadArea.addEventListener('click', () => templateFile.click());
    setupDragDrop(templateUploadArea, templateFile, 'template');
    
    templateFile.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            handleFileUpload(e.target.files[0], 'template');
        }
    });
    
    // 批次选择
    document.getElementById('batchSelect').addEventListener('change', function(e) {
        currentBatch = e.target.value;
        updateGenerateButton();
    });
}

// 设置拖拽上传
function setupDragDrop(uploadArea, fileInput, type, dropHandler) {
    uploadArea.addEventListener('dragover', function(e) {
        e.preventDefault();
        uploadArea.style.borderColor = '#409eff';
        uploadArea.style.background = '#f0f9ff';
    });
    
    uploadArea.addEventListener('dragleave', function() {
        uploadArea.style.borderColor = '#dcdfe6';
        uploadArea.style.background = '#fafafa';
    });
    
    uploadArea.addEventListener('drop', function(e) {
        e.preventDefault();
        uploadArea.style.borderColor = '#dcdfe6';
        uploadArea.style.background = '#fafafa';
        
        if (e.dataTransfer.files.length > 0) {
            if (dropHandler) {
                dropHandler(e.dataTransfer.files[0]);
            } else {
                handleFileUpload(e.dataTransfer.files[0], type);
            }
        }
    });
}

// 根据文件名返回报告的 MIME 类型
function getReportMimeType(filename) {
    const lower = (filename || '').toLowerCase();
    if (lower.endsWith('.xlsx') || lower.endsWith('.xls')) {
        return 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
    }
    return 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
}

// 处理文件上传
async function handleFileUpload(file, type) {
    // 检查文件大小
    if (file.size > 50 * 1024 * 1024) {
        alert('文件大小不能超过 50MB');
        return;
    }
    
    // 检查文件类型
    if (type === 'excel') {
        const isExcel = file.name.toLowerCase().endsWith('.xlsx') || 
                       file.name.toLowerCase().endsWith('.xls');
        if (!isExcel) {
            alert('只能上传 Excel 文件 (.xlsx, .xls)');
            return;
        }
    } else {
        const name = file.name.toLowerCase();
        const isDocx = name.endsWith('.docx');
        const isExcel = name.endsWith('.xlsx') || name.endsWith('.xls');
        if (!isDocx && !isExcel) {
            alert('只能上传 Word (.docx) 或 Excel (.xlsx, .xls) 模板文件');
            return;
        }
    }
    
    const formData = new FormData();
    formData.append('file', file);
    
    const endpoint = type === 'excel' ? '/api/upload/excel' : '/api/upload/template';
    const infoEl = document.getElementById(type + 'FileInfo');
    
    try {
        // 显示上传状态
        infoEl.innerHTML = '正在上传...';
        infoEl.style.display = 'block';
        
        const response = await fetch(endpoint, {
            method: 'POST',
            body: formData
        });
        
        const data = await response.json();
        
        if (data.success) {
            if (type === 'excel') {
                currentExcelFile = data;
                infoEl.innerHTML = `✅ ${data.filename} - 上传成功`;
                
                // 更新批次选择
                if (data.data && data.data.batch_numbers) {
                    updateBatchSelect(data.data.batch_numbers);
                }
            } else {
                currentTemplateFile = data;
                infoEl.innerHTML = `✅ ${data.filename} - 上传成功`;
            }
            
            updateGenerateButton();
            showMessage('文件上传成功!', 'success');
        } else {
            infoEl.innerHTML = `❌ ${data.error || '上传失败'}`;
            showMessage(data.error || '上传失败', 'error');
        }
    } catch (error) {
        infoEl.innerHTML = '❌ 上传失败';
        showMessage('上传失败: ' + error.message, 'error');
    }
}

// 更新批次选择
function updateBatchSelect(batches) {
    currentBatches = batches;
    const select = document.getElementById('batchSelect');
    select.innerHTML = '<option value="">请选择批次号</option>';
    
    batches.forEach(batch => {
        const option = document.createElement('option');
        option.value = batch;
        option.textContent = batch;
        select.appendChild(option);
    });
    
    document.getElementById('batchSection').style.display = 'block';
    
    // 自动选择第一个批次
    if (batches.length > 0) {
        select.value = batches[0];
        currentBatch = batches[0];
        updateGenerateButton();
    }
}

// 更新生成按钮状态
function updateGenerateButton() {
    const canGenerate = currentExcelFile && currentTemplateFile && currentBatch;
    const generateBtn = document.getElementById('generateBtn');
    const generateHint = document.getElementById('generateHint');
    
    generateBtn.disabled = !canGenerate;
    generateHint.style.display = canGenerate ? 'none' : 'block';
}

// 生成报告
async function generateReport() {
    if (!currentExcelFile || !currentTemplateFile || !currentBatch) {
        alert('请先上传 Excel 数据文件和 Word 模板文件，并选择批次号');
        return;
    }
    
    const generateBtn = document.getElementById('generateBtn');
    const originalText = generateBtn.innerHTML;
    generateBtn.innerHTML = '生成中...';
    generateBtn.disabled = true;
    
    try {
        showMessage('正在生成报告...', 'info');
        
        const response = await fetch('/api/generate', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                excel_file_id: currentExcelFile.file_id,
                template_file_id: currentTemplateFile.file_id,
                batch_number: currentBatch
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            // 从响应中获取报告内容并保存
            await saveReportFromResponse(data);
            showResult(data);
            showMessage('报告生成成功!', 'success');
        } else {
            showMessage(data.error || '生成失败', 'error');
        }
    } catch (error) {
        showMessage('生成报告时出错: ' + error.message, 'error');
    } finally {
        generateBtn.innerHTML = originalText;
        generateBtn.disabled = false;
        updateGenerateButton();
    }
}

// 从API响应保存报告
async function saveReportFromResponse(data) {
    if (!data.report_content) {
        alert('报告内容为空');
        return;
    }
    
    try {
        // Base64 解码
        const binaryString = atob(data.report_content);
        const bytes = new Uint8Array(binaryString.length);
        for (let i = 0; i < binaryString.length; i++) {
            bytes[i] = binaryString.charCodeAt(i);
        }
        
        // 创建 Blob
        const blob = new Blob([bytes], { type: getReportMimeType(data.output_filename) });
        
        // 创建下载链接
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = data.output_filename;
        
        // 触发点击下载（会弹出保存对话框）
        document.body.appendChild(a);
        a.click();
        
        // 清理
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
        
    } catch (error) {
        console.error('保存报告失败:', error);
        alert('保存报告失败: ' + error.message);
    }
}

// 显示结果
function showResult(data) {
    const resultArea = document.getElementById('resultArea');
    resultArea.innerHTML = `
        <div style="text-align: center; padding: 20px;">
            <div style="font-size: 48px; color: #67c23a; margin-bottom: 15px;">✅</div>
            <h3 style="color: #67c23a; margin-bottom: 10px;">报告生成成功!</h3>
            <p style="color: #606266; margin-bottom: 5px;">批次: ${data.statistics.batch_number}</p>
            <p style="color: #606266; margin-bottom: 5px;">参数替换: ${data.statistics.parameter_replacements}</p>
            <p style="color: #606266; margin-bottom: 5px;">文档替换: ${data.statistics.document_replacements}</p>
            <p style="color: #606266; margin-bottom: 20px;">总替换数: ${data.statistics.total_replacements}</p>
            <p style="color: #909399; font-size: 14px; margin-top: 10px;">
                文件已弹出保存对话框，请选择保存位置
            </p>
            <button onclick="saveReportAgain('${data.output_filename}', '${data.report_content}')" 
                    style="background: #409eff; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; font-size: 16px; margin-top: 10px;">
                ⬇️ 重新保存报告
            </button>
        </div>
    `;
}

// 重新保存报告（如果用户错过了保存对话框）
function saveReportAgain(filename, base64Content) {
    try {
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
        
        showMessage('重新保存成功!', 'success');
    } catch (error) {
        alert('重新保存失败: ' + error.message);
    }
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
let consistencyExcelFile = null;
let consistencyBatches = [];
let consistencyParams = [];
let consistencySelectedBatches = new Set();
let consistencySelectedParamIds = new Set();

function switchPage(name) {
    const homePage = document.getElementById('homePage');
    const consistencyPage = document.getElementById('consistencyPage');
    const homeBtn = document.getElementById('navHomeBtn');
    const consistencyBtn = document.getElementById('navConsistencyBtn');
    const showHome = name === 'home';
    homePage.style.display = showHome ? '' : 'none';
    consistencyPage.style.display = showHome ? 'none' : '';
    homeBtn.classList.toggle('active', showHome);
    consistencyBtn.classList.toggle('active', !showHome);
}

function initConsistency() {
    const uploadArea = document.getElementById('consistencyExcelUploadArea');
    const fileInput = document.getElementById('consistencyExcelFile');

    uploadArea.addEventListener('click', () => fileInput.click());
    setupDragDrop(uploadArea, fileInput, 'consistency-excel', handleConsistencyExcelUpload);

    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            handleConsistencyExcelUpload(e.target.files[0]);
        }
    });

    document.getElementById('consistencyImportFile').addEventListener('change', importConsistencyConfig);
}

async function handleConsistencyExcelUpload(file) {
    if (file.size > 50 * 1024 * 1024) {
        alert('文件大小不能超过 50MB');
        return;
    }

    const name = file.name.toLowerCase();
    if (!name.endsWith('.xlsx') && !name.endsWith('.xls')) {
        alert('只能上传 Excel 文件 (.xlsx, .xls)');
        return;
    }

    const infoEl = document.getElementById('consistencyExcelFileInfo');
    infoEl.innerHTML = '正在上传...';
    infoEl.style.display = 'block';

    const formData = new FormData();
    formData.append('file', file);

    try {
        const response = await fetch('/api/upload/excel', {
            method: 'POST',
            body: formData
        });
        const data = await response.json();

        if (data.success) {
            consistencyExcelFile = data;
            infoEl.innerHTML = `✅ ${data.filename} - 上传成功`;
            showMessage('Excel 文件上传成功!', 'success');
            await loadConsistencyParameters(data.file_id);
        } else {
            infoEl.innerHTML = `❌ ${data.error || '上传失败'}`;
            showMessage(data.error || '上传失败', 'error');
        }
    } catch (error) {
        infoEl.innerHTML = '❌ 上传失败';
        showMessage('上传失败: ' + error.message, 'error');
    }
}

async function loadConsistencyParameters(fileId) {
    try {
        const response = await fetch('/api/consistency/parameters', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ excel_file_id: fileId })
        });
        const data = await response.json();

        if (!data.success) {
            showMessage(data.error || '读取参数失败', 'error');
            return;
        }

        consistencyBatches = data.batch_numbers || [];
        consistencyParams = data.parameters || [];
        consistencySelectedBatches = new Set(consistencyBatches);
        consistencySelectedParamIds = new Set();

        document.getElementById('consistencyBatchSection').style.display = 'block';
        document.getElementById('consistencyActionSection').style.display = 'block';
        document.getElementById('consistencyParamToolbar').style.display = 'flex';

        renderConsistencyBatches();
        renderConsistencyParams();
        updateConsistencyButtons();
    } catch (error) {
        showMessage('读取参数时出错: ' + error.message, 'error');
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
    if (!consistencyExcelFile) {
        showMessage('请先上传 Excel 数据文件', 'warning');
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
                excel_file_id: consistencyExcelFile.file_id,
                batch_numbers: [...consistencySelectedBatches],
                parameter_ids: [...consistencySelectedParamIds]
            })
        });
        const data = await response.json();

        if (data.success) {
            downloadDocxFromData(data);
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

function downloadDocxFromData(data) {
    const binaryString = atob(data.report_content);
    const bytes = new Uint8Array(binaryString.length);
    for (let i = 0; i < binaryString.length; i++) {
        bytes[i] = binaryString.charCodeAt(i);
    }

    const blob = new Blob([bytes], { type: getReportMimeType(data.output_filename) });
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = data.output_filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(url);
}
