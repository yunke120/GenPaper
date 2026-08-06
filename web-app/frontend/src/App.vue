<template>
  <div id="app">
    <el-container>
      <el-header>
        <div class="header-content">
          <h1>
            <el-icon><Document /></el-icon>
            单机数据包报告生成器
          </h1>
          <div class="subtitle">基于 Excel 数据和 Word/Excel 模板的自动化报告生成工具</div>
        </div>
      </el-header>

      <el-menu
        mode="horizontal"
        router
        :default-active="$route.path"
        class="nav-menu"
      >
        <el-menu-item index="/">报告生成</el-menu-item>
        <el-menu-item index="/consistency">数据一致性对比</el-menu-item>
      </el-menu>
      
      <el-main>
        <router-view />
      </el-main>
      
      <el-footer>
        <div class="footer-content">
          <p>© 2023 单机数据包报告生成器 | 访问地址: <code>http://127.0.0.1:5000</code></p>
          <p class="version">版本: 1.0.0 | 后端状态: <span :class="statusClass">{{ backendStatus }}</span></p>
        </div>
      </el-footer>
    </el-container>
  </div>
</template>

<script>
import { Document } from '@element-plus/icons-vue'
import axios from 'axios'

export default {
  name: 'App',
  components: {
    Document
  },
  data() {
    return {
      backendStatus: '检查中...',
      statusClass: 'status-checking'
    }
  },
  mounted() {
    this.checkBackendStatus()
    // 每30秒检查一次后端状态
    setInterval(this.checkBackendStatus, 30000)
  },
  methods: {
    async checkBackendStatus() {
      try {
        const response = await axios.get('/api/health')
        if (response.data.status === 'ok') {
          this.backendStatus = '运行正常'
          this.statusClass = 'status-ok'
        } else {
          this.backendStatus = '异常'
          this.statusClass = 'status-error'
        }
      } catch (error) {
        this.backendStatus = '连接失败'
        this.statusClass = 'status-error'
      }
    }
  }
}
</script>

<style>
#app {
  font-family: Avenir, Helvetica, Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  color: #2c3e50;
  min-height: 100vh;
}

.el-header {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  padding: 20px;
  box-shadow: 0 2px 12px 0 rgba(0, 0, 0, 0.1);
}

.header-content h1 {
  margin: 0;
  font-size: 24px;
  display: flex;
  align-items: center;
  gap: 10px;
}

.header-content .subtitle {
  margin-top: 5px;
  font-size: 14px;
  opacity: 0.9;
}

.nav-menu {
  justify-content: center;
  border-bottom: 1px solid #e4e7ed;
}

.el-main {
  padding: 20px;
  background-color: #f5f7fa;
  min-height: calc(100vh - 120px);
}

.el-footer {
  background-color: #2c3e50;
  color: white;
  text-align: center;
  padding: 15px;
  font-size: 12px;
}

.footer-content {
  max-width: 1200px;
  margin: 0 auto;
}

.footer-content p {
  margin: 5px 0;
}

.footer-content code {
  background: rgba(255, 255, 255, 0.1);
  padding: 2px 6px;
  border-radius: 3px;
  font-family: monospace;
}

.version {
  font-size: 11px;
  opacity: 0.8;
}

.status-checking {
  color: #e6a23c;
}

.status-ok {
  color: #67c23a;
}

.status-error {
  color: #f56c6c;
}
</style>
