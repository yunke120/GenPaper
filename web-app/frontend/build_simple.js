// 简单的前端构建脚本
const fs = require('fs');
const path = require('path');

console.log('构建单机数据包报告生成器前端...');

const distDir = path.join(__dirname, 'dist');
if (!fs.existsSync(distDir)) {
  fs.mkdirSync(distDir, { recursive: true });
}

// 创建简单的 index.html
const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>单机数据包报告生成器</title>
    <link rel="stylesheet" href="https://unpkg.com/element-plus/dist/index.css">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: Arial, sans-serif; background: #f5f7fa; }
        .app { max-width: 1200px; margin: 20px auto; padding: 20px; }
        .header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; border-radius: 10px; margin-bottom: 20px; }
        .header h1 { margin-bottom: 10px; }
        .content { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        .card { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 12px rgba(0,0,0,0.1); }
        .upload { border: 2px dashed #dcdfe6; padding: 40px; text-align: center; margin: 15px 0; cursor: pointer; }
        .upload:hover { border-color: #409eff; }
        button { background: #409eff; color: white; border: none; padding: 12px 24px; border-radius: 4px; cursor: pointer; width: 100%; margin-top: 20px; }
        button:disabled { background: #c0c4cc; cursor: not-allowed; }
        .file-info { background: #f0f9ff; padding: 10px; margin: 10px 0; border-radius: 4px; border-left: 4px solid #409eff; }
        .footer { text-align: center; margin-top: 30px; color: #909399; font-size: 12px; }
    </style>
</head>
<body>
    <div class="app">
        <div class="header">
            <h1>单机数据包报告生成器</h1>
            <p>上传 Excel 数据和 Word 模板生成报告</p>
        </div>
        
        <div class="content">
            <div class="card">
                <h2>文件上传</h2>
                <div>
                    <h3>1. Excel 数据文件</h3>
                    <div class="upload" id="excelUpload">点击上传 Excel (.xlsx, .xls)</div>
                    <input type="file" id="excelFile" accept=".xlsx,.xls" style="display:none">
                    <div id="excelInfo" class="file-info" style="display:none"></div>
                </div>
                
                <div>
                    <h3>2. Word 模板文件</h3>
                    <div class="upload" id="templateUpload">点击上传 Word 模板 (.docx)</div>
                    <input type="file" id="templateFile" accept=".docx" style="display:none">
                    <div id="templateInfo" class="file-info" style="display:none"></div>
                </div>
                
                <div id="batchSection" style="display:none">
                    <h3>3. 选择批次号</h3>
                    <select id="batchSelect" style="width:100%;padding:10px;margin:10px 0"></select>
                </div>
                
                <button id="generateBtn" onclick="generateReport()" disabled>生成报告</button>
            </div>
            
            <div class="card">
                <h2>生成结果</h2>
                <div id="result" style="min-height:300px;display:flex;align-items:center;justify-content:center;color:#909399">
                    结果将显示在这里
                </div>
            </div>
        </div>
        
        <div class="footer">
            <p>访问地址: http://127.0.0.1:5000 | 状态: <span id="status">检查中...</span></p>
        </div>
    </div>
    
    <script src="https://unpkg.com/vue@3/dist/vue.global.js"></script>
    <script src="https://unpkg.com/element-plus"></script>
    <script src="app.js"></script>
</body>
</html>`;

fs.writeFileSync(path.join(distDir, 'index.html'), html, 'utf8');

// 创建简单的 app.js
const js = `// 单机数据包报告生成器前端
const { createApp, ref } = Vue;
const { ElMessage } = ElementPlus;

const app = createApp({
  setup() {
    const excelFile = ref(null);
    const templateFile = ref(null);
    const selectedBatch = ref('');
    const batches = ref([]);
    const result = ref(null);
    const status = ref('检查中...');
    
    // 检查后端状态
    async function checkStatus() {
      try {
        const res = await fetch('/api/health');
        const data = await res.json();
        status.value = data.status === 'ok' ? '运行正常' : '异常';
      } catch {
        status.value = '连接失败';
      }
    }
    
    // 初始化上传
    function initUpload() {
      // Excel 上传
      document.getElementById('excelUpload').onclick = () => document.getElementById('excelFile').click();
      document.getElementById('excelFile').onchange = (e) => uploadFile(e.target.files[0], 'excel');
      
      // Word 上传
      document.getElementById('templateUpload').onclick = () => document.getElementById('templateFile').click();
      document.getElementById('templateFile').onchange = (e) => uploadFile(e.target.files[0], 'template');
      
      // 批次选择
      document.getElementById('batchSelect').onchange = (e) => {
        selectedBatch.value = e.target.value;
        updateGenerateButton();
      };
    }
    
    // 上传文件
    async function uploadFile(file, type) {
      if (file.size > 50 * 1024 * 1024) {
        ElMessage.error('文件不能超过50MB');
        return;
      }
      
      const formData = new FormData();
      formData.append('file', file);
      
      const endpoint = type === 'excel' ? '/api/upload/excel' : '/api/upload/template';
      const infoEl = document.getElementById(type + 'Info');
      
      try {
        infoEl.innerHTML = '上传中...';
        infoEl.style.display = 'block';
        
        const res = await fetch(endpoint, { method: 'POST', body: formData });
        const data = await res.json();
        
        if (data.success) {
          if (type === 'excel') {
            excelFile.value = data;
            batches.value = data.data?.batch_numbers || [];
            updateBatchSelect();
          } else {
            templateFile.value = data;
          }
          
          infoEl.innerHTML = \`\${data.filename} - 上传成功\`;
          ElMessage.success('上传成功');
          updateGenerateButton();
        } else {
          infoEl.innerHTML = data.error || '上传失败';
          ElMessage.error('上传失败');
        }
      } catch (error) {
        infoEl.innerHTML = '上传失败';
        ElMessage.error('上传失败');
      }
    }
    
    // 更新批次选择
    function updateBatchSelect() {
      const select = document.getElementById('batchSelect');
      select.innerHTML = '<option value="">选择批次号</option>';
      batches.value.forEach(batch => {
        const option = document.createElement('option');
        option.value = option.textContent = batch;
        select.appendChild(option);
      });
      document.getElementById('batchSection').style.display = 'block';
    }
    
    // 更新生成按钮状态
    function updateGenerateButton() {
      const canGenerate = excelFile.value && templateFile.value && selectedBatch.value;
      document.getElementById('generateBtn').disabled = !canGenerate;
    }
    
    // 生成报告
    async function generateReport() {
      const btn = document.getElementById('generateBtn');
      btn.disabled = true;
      btn.textContent = '生成中...';
      
      try {
        const res = await fetch('/api/generate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            excel_file_id: excelFile.value.file_id,
            template_file_id: templateFile.value.file_id,
            batch_number: selectedBatch.value
          })
        });
        
        const data = await res.json();
        
        if (data.success) {
          result.value = data;
          showResult();
          ElMessage.success('报告生成成功');
        } else {
          ElMessage.error(data.error || '生成失败');
        }
      } catch (error) {
        ElMessage.error('生成失败: ' + error.message);
      }
      
      btn.disabled = false;
      btn.textContent = '生成报告';
      updateGenerateButton();
    }
    
    // 显示结果
    function showResult() {
      const resultEl = document.getElementById('result');
      resultEl.innerHTML = \`
        <div style="text-align:center;padding:20px">
          <div style="font-size:48px;color:#67c23a">✅</div>
          <h3 style="color:#67c23a">报告生成成功!</h3>
          <p>批次: \${result.value.statistics.batch_number}</p>
          <p>参数替换: \${result.value.statistics.parameter_replacements}</p>
          <p>文档替换: \${result.value.statistics.document_replacements}</p>
          <p>总替换数: \${result.value.statistics.total_replacements}</p>
          <button onclick="downloadReport('\${result.value.output_filename}')" 
                  style="background:#409eff;color:white;border:none;padding:10px 20px;border-radius:4px;cursor:pointer;margin-top:20px">
            ⬇️ 下载报告
          </button>
        </div>
      \`;
    }
    
    // 下载报告
    window.downloadReport = function(filename) {
      window.open('/api/download/' + filename);
    };
    
    // 初始化
    onMounted(() => {
      initUpload();
      checkStatus();
      setInterval(checkStatus, 30000);
    });
    
    return {
      excelFile,
      templateFile,
      selectedBatch,
      batches,
      result,
      status,
      checkStatus,
      initUpload,
      uploadFile,
      updateBatchSelect,
      updateGenerateButton,
      generateReport,
      showResult
    };
  }
});

app.use(ElementPlus);
app.mount('.app');`;

fs.writeFileSync(path.join(distDir, 'app.js'), js, 'utf8');

console.log('✅ 前端构建完成!');
console.log('📁 文件位置: frontend/dist/');
console.log('🚀 现在可以运行: web-app\\启动优化版.bat');
