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
function setupDragDrop(uploadArea, fileInput, type) {
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
            handleFileUpload(e.dataTransfer.files[0], type);
        }
    });
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
        const isDocx = file.name.toLowerCase().endsWith('.docx');
        if (!isDocx) {
            alert('只能上传 Word 文件 (.docx)');
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
        const blob = new Blob([bytes], { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' });
        
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
        
        const blob = new Blob([bytes], { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' });
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