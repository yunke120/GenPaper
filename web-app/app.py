#!/usr/bin/env python
"""
单机数据包报告生成器 - Flask Web 后端（内存版）
在内存中处理文件，不保存到磁盘，生成报告后由前端弹出保存对话框
"""

import os
import re
import sys
import hashlib
import base64
import io
import logging
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, send_from_directory

# 添加当前目录到系统路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import report_generator  # noqa: E402
from report_generator import get_template_kind  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


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

# 配置
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB

# 内存缓存：内容 MD5 去重 + 超时未使用过期。
# Flask 开发服务器默认多线程，dict 单操作在 CPython 下原子，
# 容量判断与驱逐之间存在良性竞态，最多短暂超出 MAX_CACHE_SIZE。
file_cache = {}
MAX_CACHE_SIZE = 20  # 最大缓存文件数（超出后按最久未使用驱逐）
CACHE_TTL = timedelta(minutes=30)


def add_to_cache(file_content, filename):
    """添加文件到缓存，返回文件 ID（同内容文件复用同一 ID）"""
    file_id = hashlib.md5(file_content).hexdigest()

    file_cache[file_id] = {
        'content': file_content,
        'filename': filename,
        'timestamp': datetime.now()
    }

    _evict_cache()
    return file_id


def _evict_cache():
    """清理过期条目，并在超容量时按最久未使用驱逐"""
    now = datetime.now()
    expired = [key for key, entry in file_cache.items()
               if now - entry['timestamp'] > CACHE_TTL]
    for key in expired:
        file_cache.pop(key, None)

    while len(file_cache) > MAX_CACHE_SIZE:
        oldest_key = min(file_cache, key=lambda k: file_cache[k]['timestamp'])
        file_cache.pop(oldest_key, None)


def get_from_cache(file_id):
    """从缓存获取文件；超过 TTL 未使用的条目视为过期"""
    entry = file_cache.get(file_id)
    if not entry:
        return None
    if datetime.now() - entry['timestamp'] > CACHE_TTL:
        file_cache.pop(file_id, None)
        return None
    entry['timestamp'] = datetime.now()  # 触碰续期
    return entry


def allowed_file(filename, file_type):
    """检查文件扩展名是否允许"""
    if file_type == 'excel':
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'xlsx', 'xls'}
    elif file_type == 'template':
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'docx', 'xlsx'}
    return False


# Windows 保留设备名，作为下载文件名时需要规避
_WINDOWS_RESERVED_NAMES = {
    'CON', 'PRN', 'AUX', 'NUL',
    *(f'COM{i}' for i in range(1, 10)),
    *(f'LPT{i}' for i in range(1, 10)),
}


def build_safe_output_filename(resolved_name, batch_number, output_ext):
    """由文档名称构建安全的输出文件名，失败时回退到默认命名"""
    default_name = f"数据报告_{batch_number}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{output_ext}"
    if not resolved_name:
        return default_name

    safe_name = re.sub(r'[<>:"/\\|?*\r\n\t]', '_', resolved_name).strip().rstrip('.')
    if safe_name and safe_name.split('.')[0].upper() in _WINDOWS_RESERVED_NAMES:
        safe_name = f'_{safe_name}'
    return f"{safe_name}{output_ext}" if safe_name else default_name


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

    except ValueError as e:
        # 解析/校验类错误，信息对用户可操作，直接透出
        return jsonify({'error': str(e)}), 400
    except Exception:
        logger.exception("解析 Excel 文件失败")
        return jsonify({'error': '解析 Excel 文件失败，请确认文件包含"数据"sheet 且格式正确（详情见服务端日志）'}), 500


@app.route('/api/upload/template', methods=['POST'])
def upload_template():
    """上传 Word/Excel 模板文件（内存处理）"""
    if 'file' not in request.files:
        return jsonify({'error': '没有文件上传'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': '没有选择文件'}), 400

    if not allowed_file(file.filename, 'template'):
        return jsonify({'error': '文件类型不支持，请上传 Word (.docx) 或 Excel (.xlsx) 模板文件'}), 400

    try:
        # 读取文件内容到内存
        file_content = file.read()

        # 生成文件ID
        file_id = add_to_cache(file_content, file.filename)

        return jsonify({
            'success': True,
            'filename': file.filename,
            'file_type': get_template_kind(file.filename),
            'file_id': file_id
        })

    except Exception:
        logger.exception("上传模板文件失败")
        return jsonify({'error': '上传文件失败（详情见服务端日志）'}), 500


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
        return jsonify({'error': 'Excel 文件不存在或已过期（超过30分钟未使用）'}), 404
    if not template_file:
        return jsonify({'error': '模板文件不存在或已过期（超过30分钟未使用），请重新上传'}), 404

    try:
        # 解析 Excel 数据
        excel_stream = io.BytesIO(excel_file['content'])
        excel_data = report_generator.parse_excel_data(excel_stream)

        # 检查批次号是否存在
        if batch_number not in excel_data['batch_numbers']:
            return jsonify({'error': f'批次号 "{batch_number}" 不存在'}), 400

        # 根据模板类型生成输出文件名
        template_filename = template_file['filename']
        template_type = get_template_kind(template_filename)
        output_ext = '.xlsx' if template_type == 'excel' else '.docx'

        # 模板文件名作为文档占位符（如 Doc_3.docx），解析对应报告名称作为保存文件名
        template_stem = os.path.splitext(os.path.basename(template_filename))[0]
        resolved_name = report_generator.get_document_name(
            excel_data['documents'], template_stem, batch_number
        )
        output_filename = build_safe_output_filename(resolved_name, batch_number, output_ext)

        # 准备输入输出流
        template_stream = io.BytesIO(template_file['content'])
        output_stream = io.BytesIO()

        # 生成报告到内存
        success, param_count, doc_count, error_msg = report_generator.generate_report(
            template_stream,
            batch_number,
            excel_data['parameters'],
            excel_data['documents'],
            output_stream,
            template_filename
        )

        if success:
            # 获取生成的报告内容
            report_content = output_stream.getvalue()

            # 返回报告内容（base64编码）
            report_base64 = base64.b64encode(report_content).decode('utf-8')

            return jsonify({
                'success': True,
                'output_filename': output_filename,
                'file_type': template_type,
                'report_content': report_base64,  # base64编码的报告内容
                'statistics': {
                    'batch_number': batch_number,
                    'parameter_replacements': param_count,
                    'document_replacements': doc_count,
                    'total_replacements': param_count + doc_count
                }
            })
        else:
            return jsonify({'error': error_msg or '报告生成失败'}), 500

    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception:
        logger.exception("生成报告时出错")
        return jsonify({'error': '生成报告时出错，请查看服务端日志'}), 500


@app.route('/api/consistency/parameters', methods=['POST'])
def consistency_parameters():
    """读取 Excel 数据中的所有批次与参数（含描述），用于数据一致性对比"""
    data = request.json
    if not data or 'excel_file_id' not in data:
        return jsonify({'error': '缺少 Excel 文件 ID'}), 400

    excel_file = get_from_cache(data['excel_file_id'])
    if not excel_file:
        return jsonify({'error': 'Excel 文件不存在或已过期（超过30分钟未使用）'}), 404

    try:
        excel_stream = io.BytesIO(excel_file['content'])
        excel_data = report_generator.parse_excel_data(excel_stream)

        parameters = [
            {
                'id': param['id'],
                'description': param['description'],
                'values': param['data']
            }
            for param in excel_data['parameters']
        ]

        return jsonify({
            'success': True,
            'batch_numbers': excel_data['batch_numbers'],
            'parameters': parameters
        })
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception:
        logger.exception("读取参数失败")
        return jsonify({'error': '读取参数失败，请查看服务端日志'}), 500


@app.route('/api/consistency/compare', methods=['POST'])
def consistency_compare():
    """按选中的批次与参数生成数据一致性对比 Word 报告"""
    data = request.json
    if not data:
        return jsonify({'error': '请求数据为空'}), 400

    excel_file_id = data.get('excel_file_id')
    batch_numbers = data.get('batch_numbers') or []
    parameter_ids = data.get('parameter_ids') or []

    if not excel_file_id:
        return jsonify({'error': '缺少 Excel 文件 ID'}), 400
    if not batch_numbers:
        return jsonify({'error': '请至少选择一个批次'}), 400
    if not parameter_ids:
        return jsonify({'error': '请至少选择一个参数'}), 400

    excel_file = get_from_cache(excel_file_id)
    if not excel_file:
        return jsonify({'error': 'Excel 文件不存在或已过期（超过30分钟未使用）'}), 404

    try:
        excel_stream = io.BytesIO(excel_file['content'])
        excel_data = report_generator.parse_excel_data(excel_stream)

        invalid_batches = [b for b in batch_numbers if b not in excel_data['batch_numbers']]
        if invalid_batches:
            return jsonify({'error': f'批次号不存在: {", ".join(invalid_batches)}'}), 400

        output_stream = io.BytesIO()
        success, param_count, inconsistent_count, error_msg = report_generator.generate_consistency_report(
            excel_data['parameters'],
            batch_numbers,
            parameter_ids,
            output_stream
        )

        if not success:
            return jsonify({'error': error_msg or '生成一致性报告失败'}), 500

        output_filename = f"数据一致性对比_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
        report_base64 = base64.b64encode(output_stream.getvalue()).decode('utf-8')

        return jsonify({
            'success': True,
            'output_filename': output_filename,
            'file_type': 'docx',
            'report_content': report_base64,
            'statistics': {
                'batch_count': len(batch_numbers),
                'parameter_count': param_count,
                'inconsistent_count': inconsistent_count
            }
        })
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception:
        logger.exception("生成一致性报告时出错")
        return jsonify({'error': '生成一致性报告时出错，请查看服务端日志'}), 500


@app.route('/api/preview', methods=['POST'])
def preview_data():
    """预览 Excel 数据（内存处理）"""
    data = request.json
    if not data or 'excel_file_id' not in data:
        return jsonify({'error': '缺少 Excel 文件 ID'}), 400

    excel_file = get_from_cache(data['excel_file_id'])
    if not excel_file:
        return jsonify({'error': 'Excel 文件不存在或已过期（超过30分钟未使用）'}), 404

    try:
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

    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception:
        logger.exception("预览数据时出错")
        return jsonify({'error': '预览数据时出错，请查看服务端日志'}), 500


@app.errorhandler(404)
def not_found(e):
    """处理 404 错误：API 返回 JSON；无扩展名路径回退前端入口；其余按 404 处理"""
    if request.path.startswith('/api/'):
        return jsonify({'error': 'API 端点不存在'}), 404
    if '.' in os.path.basename(request.path):
        return jsonify({'error': 'Not Found'}), 404
    return send_from_directory(get_static_folder(), 'index.html')


@app.errorhandler(500)
def internal_error(e):
    """处理 500 错误"""
    logger.exception("服务器内部错误")
    return jsonify({'error': '服务器内部错误'}), 500


def create_app():
    """创建应用实例"""
    return app


if __name__ == '__main__':
    print(f"=" * 50)
    print(f"单机数据包报告生成器 Web 版")
    print(f"=" * 50)
    print(f"访问地址: http://127.0.0.1:5000")
    print(f"文件处理: 内存缓存（30分钟未使用过期），不保存到磁盘")
    print(f"按 Ctrl+C 停止服务")
    print(f"=" * 50)

    app.run(host='127.0.0.1', port=5000, debug=False)
