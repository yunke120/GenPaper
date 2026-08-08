<template>
  <div class="home">
    <el-row :gutter="20">
      <!-- 左侧：文件上传区域 -->
      <el-col :span="12">
        <el-card class="upload-card">
          <template #header>
            <div class="card-header">
              <el-icon><Upload /></el-icon>
              <span>文件上传</span>
            </div>
          </template>
          
          <!-- Excel 文件上传 -->
          <div class="upload-section">
            <h3>1. 上传 Excel 数据文件</h3>
            <el-upload
              class="upload-demo"
              drag
              action="/api/upload/excel"
              :on-success="handleExcelSuccess"
              :on-error="handleExcelError"
              :before-upload="beforeExcelUpload"
              :show-file-list="false"
              accept=".xlsx,.xls"
            >
              <el-icon class="el-icon--upload"><upload-filled /></el-icon>
              <div class="el-upload__text">
                拖拽 Excel 文件到此处或 <em>点击上传</em>
              </div>
              <template #tip>
                <div class="el-upload__tip">
                  支持 .xlsx 和 .xls 格式，文件大小不超过 50MB
                </div>
              </template>
            </el-upload>
            
            <div v-if="excelFile" class="file-info">
              <el-icon color="#67c23a"><CircleCheck /></el-icon>
              <span>{{ excelFile.filename }}</span>
              <el-tag type="success">已上传</el-tag>
              <el-button
                size="small"
                type="text"
                @click="previewExcelData"
                :disabled="!excelFile"
              >
                预览数据
              </el-button>
            </div>
          </div>
          
          <!-- Word 模板上传 -->
          <div class="upload-section">
            <h3>2. 上传 Word/Excel 模板文件</h3>
            <el-upload
              class="upload-demo"
              drag
              action="/api/upload/template"
              :on-success="handleTemplateSuccess"
              :on-error="handleTemplateError"
              :before-upload="beforeTemplateUpload"
              :show-file-list="false"
              accept=".docx,.xlsx"
            >
              <el-icon class="el-icon--upload"><upload-filled /></el-icon>
              <div class="el-upload__text">
                拖拽 Word/Excel 模板到此处或 <em>点击上传</em>
              </div>
              <template #tip>
                <div class="el-upload__tip">
                  支持 .docx 和 .xlsx 格式，文件大小不超过 50MB
                </div>
              </template>
            </el-upload>
            
            <div v-if="templateFile" class="file-info">
              <el-icon color="#67c23a"><CircleCheck /></el-icon>
              <span>{{ templateFile.filename }}</span>
              <el-tag type="success">已上传</el-tag>
            </div>
          </div>
          
          <!-- 批次选择 -->
          <div class="upload-section" v-if="excelFile && excelFile.data">
            <h3>3. 选择批次号</h3>
            <el-select
              v-model="selectedBatch"
              placeholder="请选择批次号"
              style="width: 100%"
              size="large"
            >
              <el-option
                v-for="batch in excelFile.data.batch_numbers"
                :key="batch"
                :label="batch"
                :value="batch"
              />
            </el-select>
            
            <div class="batch-info" v-if="selectedBatch">
              <el-tag type="info">已选择: {{ selectedBatch }}</el-tag>
            </div>
          </div>
          
          <!-- 生成按钮 -->
          <div class="action-section">
            <el-button
              type="primary"
              size="large"
              :loading="generating"
              :disabled="!canGenerate"
              @click="generateReport"
              style="width: 100%"
            >
              <el-icon v-if="!generating"><MagicStick /></el-icon>
              {{ generating ? '生成中...' : '生成报告' }}
            </el-button>
            
            <div class="hint" v-if="!canGenerate">
              <el-icon><InfoFilled /></el-icon>
              请先上传 Excel 数据文件和 Word 模板文件，并选择批次号
            </div>
          </div>
        </el-card>
      </el-col>
      
      <!-- 右侧：结果展示区域 -->
      <el-col :span="12">
        <el-card class="result-card">
          <template #header>
            <div class="card-header">
              <el-icon><DataAnalysis /></el-icon>
              <span>生成结果</span>
            </div>
          </template>
          
          <!-- 数据预览 -->
          <div v-if="previewData" class="preview-section">
            <h3>数据预览</h3>
            <el-descriptions :column="1" border>
              <el-descriptions-item label="批次数量">
                {{ previewData.statistics.total_batches }}
              </el-descriptions-item>
              <el-descriptions-item label="参数总数">
                {{ previewData.statistics.total_parameters }}
              </el-descriptions-item>
              <el-descriptions-item label="分系统文档">
                {{ previewData.statistics.total_documents.分系统 }}
              </el-descriptions-item>
              <el-descriptions-item label="设计文件">
                {{ previewData.statistics.total_documents.设计文件 }}
              </el-descriptions-item>
              <el-descriptions-item label="单机文档总数">
                {{ previewData.statistics.total_documents.单机文档 }}
              </el-descriptions-item>
            </el-descriptions>
            
            <div class="preview-batches">
              <h4>批次列表:</h4>
              <el-tag
                v-for="batch in previewData.preview.batch_numbers"
                :key="batch"
                :type="batch === selectedBatch ? 'primary' : 'info'"
                class="batch-tag"
              >
                {{ batch }}
              </el-tag>
            </div>
          </div>
          
          <!-- 生成结果 -->
          <div v-if="generationResult" class="result-section">
            <h3>生成结果</h3>
            <el-result
              icon="success"
              title="报告生成成功!"
              :sub-title="`批次: ${generationResult.statistics.batch_number}`"
            >
              <template #extra>
                <el-descriptions :column="1" border>
                  <el-descriptions-item label="参数替换">
                    {{ generationResult.statistics.parameter_replacements }}
                  </el-descriptions-item>
                  <el-descriptions-item label="文档替换">
                    {{ generationResult.statistics.document_replacements }}
                  </el-descriptions-item>
                  <el-descriptions-item label="总替换数">
                    {{ generationResult.statistics.total_replacements }}
                  </el-descriptions-item>
                </el-descriptions>
                
                <div class="download-section">
                  <el-button
                    type="primary"
                    size="large"
                    @click="downloadReport"
                  >
                    <el-icon><Download /></el-icon>
                    下载报告文件
                  </el-button>
                  
                  <div class="file-info">
                    <el-icon><Document /></el-icon>
                    <span>{{ generationResult.output_filename }}</span>
                  </div>
                </div>
              </template>
            </el-result>
          </div>
          
          <!-- 空状态 -->
          <div v-if="!previewData && !generationResult" class="empty-state">
            <el-empty description="暂无数据">
              <template #image>
                <el-icon size="100"><DataLine /></el-icon>
              </template>
              <p>上传 Excel 文件后可以预览数据，生成报告后会显示结果</p>
            </el-empty>
          </div>
        </el-card>
      </el-col>
    </el-row>
    
    <!-- 数据预览对话框 -->
    <el-dialog
      v-model="previewDialogVisible"
      title="Excel 数据预览"
      width="80%"
    >
      <div v-if="previewData">
        <el-tabs v-model="activePreviewTab">
          <el-tab-pane label="参数数据" name="parameters">
            <el-table
              :data="previewData.preview.parameters"
              border
              style="width: 100%"
              height="400"
            >
              <el-table-column prop="id" label="参数ID" width="120" />
              <el-table-column prop="description" label="描述" />
              <el-table-column
                v-for="batch in previewData.preview.batch_numbers.slice(0, 3)"
                :key="batch"
                :label="batch"
                width="120"
              >
                <template #default="scope">
                  {{ scope.row.data_sample[batch] || '-' }}
                </template>
              </el-table-column>
            </el-table>
            <div class="preview-note">
              显示前10个参数作为示例，共 {{ previewData.statistics.total_parameters }} 个参数
            </div>
          </el-tab-pane>
          
          <el-tab-pane label="文档数据" name="documents">
            <el-tabs type="border-card">
              <el-tab-pane label="分系统">
                <el-table
                  :data="Object.entries(previewData.preview.documents.分系统 || {})"
                  border
                  style="width: 100%"
                  height="200"
                >
                  <el-table-column prop="0" label="文档ID" width="120" />
                  <el-table-column prop="1" label="文档名称" />
                </el-table>
              </el-tab-pane>
              
              <el-tab-pane label="设计文件">
                <el-table
                  :data="Object.entries(previewData.preview.documents.设计文件 || {})"
                  border
                  style="width: 100%"
                  height="200"
                >
                  <el-table-column prop="0" label="文档ID" width="120" />
                  <el-table-column prop="1" label="文档名称" />
                </el-table>
              </el-tab-pane>
              
              <el-tab-pane
                v-for="batch in previewData.preview.batch_numbers"
                :key="batch"
                :label="batch"
              >
                <el-table
                  :data="Object.entries(previewData.preview.documents.单机文档[batch] || {})"
                  border
                  style="width: 100%"
                  height="200"
                >
                  <el-table-column prop="0" label="文档ID" width="120" />
                  <el-table-column prop="1" label="文档名称" />
                </el-table>
              </el-tab-pane>
            </el-tabs>
          </el-tab-pane>
        </el-tabs>
      </div>
    </el-dialog>
  </div>
</template>

<script>
import {
  Upload,
  UploadFilled,
  CircleCheck,
  MagicStick,
  InfoFilled,
  DataAnalysis,
  Download,
  Document,
  DataLine
} from '@element-plus/icons-vue'
import axios from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'

export default {
  name: 'HomeView',
  components: {
    Upload,
    UploadFilled,
    CircleCheck,
    MagicStick,
    InfoFilled,
    DataAnalysis,
    Download,
    Document,
    DataLine
  },
  data() {
    return {
      excelFile: null,
      templateFile: null,
      selectedBatch: '',
      generating: false,
      previewData: null,
      generationResult: null,
      previewDialogVisible: false,
      activePreviewTab: 'parameters'
    }
  },
  computed: {
    canGenerate() {
      return this.excelFile && this.templateFile && this.selectedBatch
    }
  },
  methods: {
    beforeExcelUpload(file) {
      const isExcel = file.type.includes('excel') || 
                     file.name.endsWith('.xlsx') || 
                     file.name.endsWith('.xls')
      const isLt50M = file.size / 1024 / 1024 < 50
      
      if (!isExcel) {
        ElMessage.error('只能上传 Excel 文件!')
        return false
      }
      if (!isLt50M) {
        ElMessage.error('文件大小不能超过 50MB!')
        return false
      }
      return true
    },
    
    beforeTemplateUpload(file) {
      const name = file.name.toLowerCase()
      const isDocx = file.type.includes('document') || name.endsWith('.docx')
      const isExcel = name.endsWith('.xlsx')
      const isLt50M = file.size / 1024 / 1024 < 50
      
      if (!isDocx && !isExcel) {
        ElMessage.error('只能上传 Word (.docx) 或 Excel (.xlsx) 模板文件!')
        return false
      }
      if (!isLt50M) {
        ElMessage.error('文件大小不能超过 50MB!')
        return false
      }
      return true
    },
    
    handleExcelSuccess(response, file) {
      if (response.success) {
        this.excelFile = {
          filename: response.filename,
          file_id: response.file_id,
          data: response.data
        }
        ElMessage.success('Excel 文件上传成功!')
        
        // 自动选择第一个批次
        if (response.data.batch_numbers.length > 0) {
          this.selectedBatch = response.data.batch_numbers[0]
        }
      } else {
        ElMessage.error(response.error || '上传失败')
      }
    },
    
    handleExcelError(error) {
      ElMessage.error('Excel 文件上传失败: ' + (error.message || '未知错误'))
    },
    
    handleTemplateSuccess(response, file) {
      if (response.success) {
        this.templateFile = {
          filename: response.filename,
          file_id: response.file_id
        }
        ElMessage.success('Word 模板上传成功!')
      } else {
        ElMessage.error(response.error || '上传失败')
      }
    },
    
    handleTemplateError(error) {
      ElMessage.error('Word 模板上传失败: ' + (error.message || '未知错误'))
    },
    
    async previewExcelData() {
      if (!this.excelFile) {
        ElMessage.warning('请先上传 Excel 文件')
        return
      }
      
      try {
        const response = await axios.post('/api/preview', {
          excel_file_id: this.excelFile.file_id
        })
        
        if (response.data.success) {
          this.previewData = response.data
          this.previewDialogVisible = true
        } else {
          ElMessage.error(response.data.error || '预览失败')
        }
      } catch (error) {
        ElMessage.error('预览数据时出错: ' + error.message)
      }
    },
    
    async generateReport() {
      if (!this.canGenerate) {
        ElMessage.warning('请先完成所有必要步骤')
        return
      }
      
      this.generating = true
      
      try {
        const response = await axios.post('/api/generate', {
          excel_file_id: this.excelFile.file_id,
          template_file_id: this.templateFile.file_id,
          batch_number: this.selectedBatch
        })
        
        if (response.data.success) {
          this.generationResult = response.data
          ElMessage.success('报告生成成功!')
        } else {
          ElMessage.error(response.data.error || '生成失败')
        }
      } catch (error) {
        ElMessage.error('生成报告时出错: ' + error.message)
      } finally {
        this.generating = false
      }
    },
    
    downloadReport() {
      if (!this.generationResult || !this.generationResult.report_content) {
        ElMessage.warning('没有可下载的报告')
        return
      }

      const binaryString = atob(this.generationResult.report_content)
      const bytes = new Uint8Array(binaryString.length)
      for (let i = 0; i < binaryString.length; i++) {
        bytes[i] = binaryString.charCodeAt(i)
      }
      
      const filename = this.generationResult.output_filename
      const isExcel = filename.toLowerCase().endsWith('.xlsx') ||
                      filename.toLowerCase().endsWith('.xls')
      const mimeType = isExcel
        ? 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        : 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
      const blob = new Blob([bytes], { type: mimeType })
      const url = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = filename
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      window.URL.revokeObjectURL(url)
    }
  }
}
</script>

<style scoped>
.home {
  max-width: 1400px;
  margin: 0 auto;
}

.upload-card,
.result-card {
  height: 100%;
  min-height: 600px;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 18px;
  font-weight: bold;
}

.upload-section {
  margin-bottom: 30px;
}

.upload-section h3 {
  margin-bottom: 15px;
  color: #333;
  font-size: 16px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.upload-demo {
  margin-bottom: 15px;
}

.file-info {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
  padding: 10px;
  background-color: #f8f9fa;
  border-radius: 4px;
  border-left: 4px solid #67c23a;
}

.batch-info {
  margin-top: 15px;
}

.action-section {
  margin-top: 40px;
}

.action-section .hint {
  margin-top: 15px;
  padding: 10px;
  background-color: #f0f9ff;
  border-radius: 4px;
  color: #409eff;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
}

.preview-section,
.result-section {
  margin-bottom: 30px;
}

.preview-section h3,
.result-section h3 {
  margin-bottom: 15px;
  color: #333;
  font-size: 16px;
}

.preview-batches {
  margin-top: 20px;
}

.batch-tag {
  margin: 5px;
}

.download-section {
  margin-top: 20px;
  text-align: center;
}

.download-section .file-info {
  margin-top: 15px;
  justify-content: center;
}

.empty-state {
  text-align: center;
  padding: 60px 0;
}

.preview-note {
  margin-top: 10px;
  color: #909399;
  font-size: 13px;
}

.preview-section h4 {
  margin: 15px 0 10px;
  color: #606266;
}
</style>
