<template>
  <div class="consistency">
    <el-row :gutter="20">
      <el-col :span="10">
        <el-card class="config-card">
          <template #header>
            <div class="card-header">
              <el-icon><Files /></el-icon>
              <span>数据与配置</span>
            </div>
          </template>

          <div class="section">
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
            </div>
          </div>

          <div class="section" v-if="batchNumbers.length">
            <h3>2. 勾选对比批次</h3>
            <el-checkbox-group v-model="selectedBatches">
              <el-checkbox
                v-for="batch in batchNumbers"
                :key="batch"
                :label="batch"
              >
                {{ batch }}
              </el-checkbox>
            </el-checkbox-group>
          </div>

          <div class="section" v-if="parameters.length">
            <h3>3. 配置操作</h3>
            <div class="btn-row">
              <el-button @click="exportConfig" :disabled="!canExportConfig">
                导出配置
              </el-button>
              <el-button @click="triggerConfigImport">
                导入配置
              </el-button>
              <el-button
                type="primary"
                :loading="exporting"
                :disabled="!canCompare"
                @click="exportWord"
              >
                导出Word对比
              </el-button>
            </div>
            <input
              ref="configInput"
              type="file"
              accept=".json"
              style="display: none"
              @change="importConfig"
            />
          </div>
        </el-card>
      </el-col>

      <el-col :span="14">
        <el-card class="params-card">
          <template #header>
            <div class="card-header">
              <el-icon><List /></el-icon>
              <span>参数选择</span>
            </div>
          </template>

          <el-table
            ref="paramTable"
            :data="parameters"
            border
            max-height="420"
            @selection-change="onSelectionChange"
          >
            <el-table-column type="selection" width="45" />
            <el-table-column prop="id" label="参数ID" width="150" />
            <el-table-column prop="description" label="Description" />
          </el-table>

          <div class="select-hint">
            已选 {{ selectedParameterIds.length }} 个参数
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script>
import {
  Files,
  List,
  UploadFilled,
  CircleCheck,
} from '@element-plus/icons-vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'

export default {
  name: 'ConsistencyView',
  components: {
    Files,
    List,
    UploadFilled,
    CircleCheck,
  },
  data() {
    return {
      excelFile: null,
      batchNumbers: [],
      parameters: [],
      selectedBatches: [],
      selectedParameterIds: [],
      exporting: false
    }
  },
  computed: {
    canExportConfig() {
      return this.batchNumbers.length > 0 && this.parameters.length > 0
    },
    canCompare() {
      return this.selectedBatches.length > 0 && this.selectedParameterIds.length > 0
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

    handleExcelSuccess(response) {
      if (!response.success) {
        ElMessage.error(response.error || '上传失败')
        return
      }
      this.excelFile = {
        filename: response.filename,
        file_id: response.file_id
      }
      ElMessage.success('Excel 文件上传成功!')
      this.loadParameters(response.file_id)
    },

    handleExcelError(error) {
      ElMessage.error('Excel 文件上传失败: ' + (error.message || '未知错误'))
    },

    async loadParameters(fileId) {
      try {
        const response = await axios.post('/api/consistency/parameters', {
          excel_file_id: fileId
        })
        if (!response.data.success) {
          ElMessage.error(response.data.error || '读取参数失败')
          return
        }
        this.batchNumbers = response.data.batch_numbers
        this.parameters = response.data.parameters
        this.selectedBatches = [...response.data.batch_numbers]
        this.selectedParameterIds = []
      } catch (error) {
        ElMessage.error('读取参数时出错: ' + error.message)
      }
    },

    onSelectionChange(rows) {
      this.selectedParameterIds = rows.map((row) => row.id)
    },

    syncTableSelection() {
      const table = this.$refs.paramTable
      if (!table) {
        return
      }
      const ids = new Set(this.selectedParameterIds)
      table.clearSelection()
      this.parameters.forEach((param) => {
        if (ids.has(param.id)) {
          table.toggleRowSelection(param, true)
        }
      })
    },

    exportConfig() {
      const config = {
        batch_numbers: this.selectedBatches,
        parameter_ids: this.selectedParameterIds
      }
      const blob = new Blob([JSON.stringify(config, null, 2)], {
        type: 'application/json'
      })
      const url = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `一致性对比配置_${new Date().toISOString().slice(0, 10)}.json`
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      window.URL.revokeObjectURL(url)
      ElMessage.success('配置已导出')
    },

    triggerConfigImport() {
      this.$refs.configInput.click()
    },

    importConfig(event) {
      const file = event.target.files[0]
      event.target.value = ''
      if (!file) {
        return
      }
      const reader = new FileReader()
      reader.onload = () => {
        try {
          const config = JSON.parse(reader.result)
          const batches = Array.isArray(config.batch_numbers) ? config.batch_numbers : []
          const ids = Array.isArray(config.parameter_ids) ? config.parameter_ids : []
          this.selectedBatches = batches.filter((b) => this.batchNumbers.includes(b))
          this.selectedParameterIds = ids.filter((id) =>
            this.parameters.some((p) => p.id === id)
          )
          this.syncTableSelection()
          ElMessage.success('配置已导入')
        } catch (e) {
          ElMessage.error('配置文件格式不正确')
        }
      }
      reader.readAsText(file)
    },

    async exportWord() {
      if (!this.canCompare) {
        ElMessage.warning('请先勾选批次和参数')
        return
      }
      this.exporting = true
      try {
        const response = await axios.post('/api/consistency/compare', {
          excel_file_id: this.excelFile.file_id,
          batch_numbers: this.selectedBatches,
          parameter_ids: this.selectedParameterIds
        })
        if (!response.data.success) {
          ElMessage.error(response.data.error || '生成失败')
          return
        }

        const binaryString = atob(response.data.report_content)
        const bytes = new Uint8Array(binaryString.length)
        for (let i = 0; i < binaryString.length; i++) {
          bytes[i] = binaryString.charCodeAt(i)
        }
        const blob = new Blob([bytes], {
          type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        })
        const url = window.URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = url
        link.download = response.data.output_filename
        document.body.appendChild(link)
        link.click()
        document.body.removeChild(link)
        window.URL.revokeObjectURL(url)

        const stats = response.data.statistics
        ElMessage.success(
          `对比完成: ${stats.parameter_count} 个参数, ${stats.inconsistent_count} 项不一致`
        )
      } catch (error) {
        ElMessage.error('生成一致性报告时出错: ' + error.message)
      } finally {
        this.exporting = false
      }
    }
  }
}
</script>

<style scoped>
.consistency {
  max-width: 1400px;
  margin: 0 auto;
}

.config-card,
.params-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 18px;
  font-weight: bold;
}

.section {
  margin-bottom: 24px;
}

.section h3 {
  margin-bottom: 12px;
  color: #333;
  font-size: 15px;
}

.upload-demo {
  margin-bottom: 12px;
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

.btn-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.select-hint {
  margin-top: 10px;
  color: #909399;
  font-size: 13px;
}
</style>
