#!/usr/bin/env python
"""
单机数据包报告生成器 - Flask Web 后端（内存版）
在内存中处理文件，不保存到磁盘，生成报告后弹出保存对话框
"""

import os
import sys
import json
import hashlib
import base64
import io
from datetime import datetime
from flask import Flask, request, jsonify, send_file, send_from_directory, make_response
from flask_cors import CORS

# 添加当前目录到系统路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def get_static_folder():
    """获取静态文件目录（兼容打包后的路径）"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 创建的临时目录
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, 'frontend', 'dist')

# 创建 Flask 应用
app = Flask(__name__, static_folder=get_static_folder(), static_url_path='')
CORS(app)

# 配置
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB

# 内存缓存（简单实现，生产环境需要更健壮的缓存）
file_cache = {}
MAX_CACHE_SIZE = 5  # 最大缓存文件数

def add_to_cache(file_content, filename):
    """添加文件到缓存，返回文件ID"""
    # 计算文件内容的哈希值作为ID
    file_hash = hashlib.md5(file_content).hexdigest()[:16]
    file_id = f"{file_hash}_{datetime.now().strftime('%H%M%S')}"
    
    # 清理缓存如果超过大小
    if len(file_cache) >= MAX_CACHE_SIZE:
        # 移除最旧的条目
        oldest_key = next(iter(file_cache))
        file_cache.pop(oldest_key, None)
    
    # 存储文件内容
    file_cache[file_id] = {
        'content': file_content,
        'filename': filename,
        'timestamp': datetime.now()
    }
    
    return file_id

def get_from_cache(file_id):
    """从缓存获取文件"""
    return file_cache.get(file_id)

def allowed_file(filename, file_type):
    """检查文件扩展名是否允许"""
    if file_type == 'excel':
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'xlsx', 'xls'}
    elif file_type == 'template':
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'docx'}
    return False

@app.route('/')
def index():
    """服务前端静态文件"""
    return send_from_directory(get_static_folder(), 'index.html')

@app.route('/api/health', methods=['GET'])
def health_check():
    """健康检查"""
    return jsonify({
        'status': 'ok',
        'timestamp': datetime.now().isoformat(),
        'memory_cache_size': len(file_cache)
    })

@app.route('/api/upload/excel', methods=['POST'])
def upload_excel():
    """上传 Excel 文件并解析数据（内存处理）"""
    if 'file' not in request.files:
        return jsonify({'error': '没有文件上传'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': '没有选择文件'}), 400
    
    if not allowed_file(file.filename, 'excel'):
        return jsonify({'error': '文件类型不支持，请上传 Excel 文件 (.xlsx, .xls)'}), 400
    
    try:
        # 读取文件内容到内存
        file_content = file.read()
        
        # 生成文件ID
        file_id = add_to_cache(file_content, file.filename)
        
        # 解析 Excel 数据
        import report_generator
        import io
        
        # 使用 BytesIO 包装文件内容
        excel_stream = io.BytesIO(file_content)
        data = report_generator.parse_excel_data(excel_stream)
        
        # 返回解析结果
        return jsonify({
            'success': True,
            'filename': file.filename,
            'data': {
                'batch_numbers': data['batch_numbers'],
                'parameter_count': len(data['parameters']),
                'documents': {
                    '通用文档': len(data['documents']['通用文档']),
                    '单机文档': {batch: len(docs) for batch, docs in data['documents']['单机文档'].items()}
                },
                'timestamp': data['timestamp']
            },
            'file_id': file_id
        })
    
    except Exception as e:
        return jsonify({'error': f'解析 Excel 文件失败: {str(e)}'}), 500

@app.route('/api/upload/template', methods=['POST'])
def upload_template():
    """上传 Word 模板文件（内存处理）"""
    if 'file' not in request.files:
        return jsonify({'error': '没有文件上传'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': '没有选择文件'}), 400
    
    if not allowed_file(file.filename, 'template'):
        return jsonify({'error': '文件类型不支持，请上传 Word 文件 (.docx)'}), 400
    
    try:
        # 读取文件内容到内存
        file_content = file.read()
        
        # 生成文件ID
        file_id = add_to_cache(file_content, file.filename)
        
        return jsonify({
            'success': True,
            'filename': file.filename,
            'file_id': file_id
        })
    
    except Exception as e:
        return jsonify({'error': f'上传文件失败: {str(e)}'}), 500

@app.route('/api/generate', methods=['POST'])
def generate():
    """生成报告（内存处理）"""
    data = request.json
    if not data:
        return jsonify({'error': '请求数据为空'}), 400
    
    excel_file_id = data.get('excel_file_id')
    template_file_id = data.get('template_file_id')
    batch_number = data.get('batch_number')
    
    if not all([excel_file_id, template_file_id, batch_number]):
        return jsonify({'error': '缺少必要参数'}), 400
    
    # 从缓存获取文件
    excel_file = get_from_cache(excel_file_id)
    template_file = get_from_cache(template_file_id)
    
    if not excel_file:
        return jsonify({'error': 'Excel 文件不存在或已过期'}), 404
    if not template_file:
        return jsonify({'error': '模板文件不存在或已过期'}), 404
    
    try:
        # 导入报告生成器
        import report_generator
        import io
        
        # 解析 Excel 数据
        excel_stream = io.BytesIO(excel_file['content'])
        excel_data = report_generator.parse_excel_data(excel_stream)
        
        # 检查批次号是否存在
        if batch_number not in excel_data['batch_numbers']:
            return jsonify({'error': f'批次号 "{batch_number}" 不存在'}), 400
        
        # 生成输出文件名
        output_filename = f"数据报告_{batch_number}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
        
        # 准备输入输出流
        template_stream = io.BytesIO(template_file['content'])
        output_stream = io.BytesIO()
        
        # 生成报告到内存
        success, param_count, doc_count = report_generator.generate_report(
            template_stream,
            batch_number,
            excel_data['parameters'],
            excel_data['documents'],
            output_stream
        )
        
        if success:
            # 获取生成的报告内容
            report_content = output_stream.getvalue()
            
            # 返回报告内容（base64编码）
            report_base64 = base64.b64encode(report_content).decode('utf-8')
            
            return jsonify({
                'success': True,
                'output_filename': output_filename,
                'report_content': report_base64,  # base64编码的报告内容
                'statistics': {
                    'batch_number': batch_number,
                    'parameter_replacements': param_count,
                    'document_replacements': doc_count,
                    'total_replacements': param_count + doc_count
                }
            })
        else:
            return jsonify({'error': '报告生成失败'}), 500
    
    except Exception as e:
        return jsonify({'error': f'生成报告时出错: {str(e)}'}), 500

@app.route('/api/preview', methods=['POST'])
def preview_data():
    """预览 Excel 数据（内存处理）"""
    data = request.json
    if not data or 'excel_file_id' not in data:
        return jsonify({'error': '缺少 Excel 文件 ID'}), 400
    
    excel_file = get_from_cache(data['excel_file_id'])
    if not excel_file:
        return jsonify({'error': 'Excel 文件不存在或已过期'}), 404
    
    try:
        import report_generator
        import io
        
        excel_stream = io.BytesIO(excel_file['content'])
        excel_data = report_generator.parse_excel_data(excel_stream)
        
        # 准备预览数据
        preview = {
            'batch_numbers': excel_data['batch_numbers'],
            'parameters': [],
            'documents': {
                '通用文档': list(excel_data['documents']['通用文档'].keys()),
                '单机文档': {}
            }
        }
        
        # 添加前10个参数作为预览
        for param in excel_data['parameters'][:10]:
            preview['parameters'].append({
                'id': param['id'],
                'description': param['description'],
                'data_sample': {batch: param['data'].get(batch, '') for batch in excel_data['batch_numbers'][:3]}
            })
        
        # 添加单机文档预览
        for batch in excel_data['batch_numbers']:
            if batch in excel_data['documents']['单机文档']:
                preview['documents']['单机文档'][batch] = list(excel_data['documents']['单机文档'][batch].keys())[:5]
        
        return jsonify({
            'success': True,
            'preview': preview,
            'statistics': {
                'total_parameters': len(excel_data['parameters']),
                'total_batches': len(excel_data['batch_numbers']),
                'total_documents': {
                    '通用文档': len(excel_data['documents']['通用文档']),
                    '单机文档': sum(len(docs) for docs in excel_data['documents']['单机文档'].values())
                }
            }
        })
    
    except Exception as e:
        return jsonify({'error': f'预览数据时出错: {str(e)}'}), 500

@app.errorhandler(404)
def not_found(e):
    """处理 404 错误"""
    if request.path.startswith('/api/'):
        return jsonify({'error': 'API 端点不存在'}), 404
    return send_from_directory(get_static_folder(), 'index.html')

@app.errorhandler(500)
def internal_error(e):
    """处理 500 错误"""
    return jsonify({'error': '服务器内部错误'}), 500

def create_app():
    """创建应用实例"""
    return app

if __name__ == '__main__':
    print(f"=" * 50)
    print(f"单机数据包报告生成器 Web 版")
    print(f"=" * 50)
    print(f"访问地址: http://127.0.0.1:5000")
    print(f"文件处理: 内存缓存，不保存到磁盘")
    print(f"按 Ctrl+C 停止服务")
    print(f"=" * 50)
    
    app.run(host='127.0.0.1', port=5000, debug=False)
