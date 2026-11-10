#!/usr/bin/env python
"""
单机数据包报告生成器 - Flask Web 后端（内存版）
在内存中处理文件，不保存到磁盘，生成报告后由前端弹出保存对话框
"""

import os
import re
import sys
import json
import hashlib
import base64
import io
import shutil
import logging
import zipfile
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, send_from_directory, send_file

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


def _resolve_consistency_source(data):
    """一致性对比数据源：优先 model（型号数据库当前数据包），兼容 excel_file_id（内存缓存）。
    返回 (parsed, error_response)。"""
    model = data.get('model')
    if model:
        try:
            parsed = _get_parsed_data(model)
        except ValueError as e:
            return None, (jsonify({'error': str(e)}), 400)
        if not parsed:
            return None, (jsonify({'error': f'型号 "{model}" 尚无数据包，请先上传'}), 400)
        return parsed, None

    excel_file_id = data.get('excel_file_id')
    if not excel_file_id:
        return None, (jsonify({'error': '缺少 model 或 excel_file_id'}), 400)
    excel_file = get_from_cache(excel_file_id)
    if not excel_file:
        return None, (jsonify({'error': 'Excel 文件不存在或已过期（超过30分钟未使用）'}), 404)
    try:
        parsed = report_generator.parse_excel_data(io.BytesIO(excel_file['content']))
    except ValueError as e:
        return None, (jsonify({'error': str(e)}), 400)
    return parsed, None


@app.route('/api/consistency/parameters', methods=['POST'])
def consistency_parameters():
    """读取数据中的所有批次与参数（含描述），用于数据一致性对比"""
    data = request.json
    if not data:
        return jsonify({'error': '请求数据为空'}), 400

    try:
        parsed, error = _resolve_consistency_source(data)
        if error:
            return error

        parameters = [
            {
                'id': param['id'],
                'description': param['description'],
                'values': param['data']
            }
            for param in parsed['parameters']
        ]

        return jsonify({
            'success': True,
            'batch_numbers': parsed['batch_numbers'],
            'parameters': parameters
        })
    except Exception:
        logger.exception("读取参数失败")
        return jsonify({'error': '读取参数失败，请查看服务端日志'}), 500


@app.route('/api/consistency/compare', methods=['POST'])
def consistency_compare():
    """按选中的批次与参数生成数据一致性对比 Word 报告"""
    data = request.json
    if not data:
        return jsonify({'error': '请求数据为空'}), 400

    batch_numbers = data.get('batch_numbers') or []
    parameter_ids = data.get('parameter_ids') or []

    if not batch_numbers:
        return jsonify({'error': '请至少选择一个批次'}), 400
    if not parameter_ids:
        return jsonify({'error': '请至少选择一个参数'}), 400

    try:
        parsed, error = _resolve_consistency_source(data)
        if error:
            return error

        invalid_batches = [b for b in batch_numbers if b not in parsed['batch_numbers']]
        if invalid_batches:
            return jsonify({'error': f'批次号不存在: {", ".join(invalid_batches)}'}), 400

        output_stream = io.BytesIO()
        success, param_count, inconsistent_count, error_msg = report_generator.generate_consistency_report(
            parsed['parameters'],
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


# ======================================================================
# 型号数据库（数据包/<型号>/）：多型号浏览、模板管理、版本管理、按批次生成
# ======================================================================

# 数据包版本快照文件名前缀
VERSION_FILE_PREFIX = '数据包_'
# 解析结果缓存：{model: (mtime, parsed)}，数据包文件按 mtime 失效
_parse_cache = {}
PARSE_CACHE_SIZE = 8


def get_data_root():
    """型号数据库根目录（数据包/<型号>/），可用环境变量 GENPAPER_DATA_ROOT 覆盖"""
    env = os.environ.get('GENPAPER_DATA_ROOT')
    if env:
        return os.path.abspath(env)
    if getattr(sys, 'frozen', False):
        return os.path.join(os.path.dirname(sys.executable), '数据包')
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(base, '..', '数据包'))


def _validate_model_name(model):
    """校验型号名称：可用作目录名，拒绝路径穿越与 Windows 保留名"""
    model = (model or '').strip()
    if not model or model in {'.', '..'}:
        raise ValueError('型号名称不能为空')
    if len(model) > 80 or re.search(r"[\\/:*?\"<>|'\x00-\x1f]", model):
        raise ValueError(f'型号名称不合法: "{model}"')
    if model.split('.')[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError(f'型号名称不能为 Windows 保留名: {model}')
    return model


def _model_root(model, must_exist=True):
    model = _validate_model_name(model)
    root = os.path.join(get_data_root(), model)
    if must_exist and not os.path.isdir(root):
        raise ValueError(f'型号 "{model}" 不存在')
    return root


def _model_templates_dir(model):
    return os.path.join(_model_root(model), 'templates')


def _model_versions_dir(model):
    return os.path.join(_model_root(model), 'versions')


def _current_data_file(model):
    """型号根下唯一的 .xlsx 数据包；无数据返回 None，多个时报错"""
    root = _model_root(model)
    files = [f for f in os.listdir(root)
             if f.lower().endswith('.xlsx') and os.path.isfile(os.path.join(root, f))]
    if not files:
        return None
    if len(files) > 1:
        raise ValueError(f'型号 "{model}" 根目录下存在多个 .xlsx 文件，请仅保留一个: {", ".join(files)}')
    return os.path.join(root, files[0])


def _file_info(path):
    return {
        'name': os.path.basename(path),
        'size': os.path.getsize(path),
        'mtime': datetime.fromtimestamp(os.path.getmtime(path)).strftime('%Y-%m-%d %H:%M:%S'),
    }


def _get_parsed_data(model):
    """解析并缓存型号当前数据包（按文件 mtime 失效）"""
    path = _current_data_file(model)
    if not path:
        return None
    mtime = os.path.getmtime(path)
    cached = _parse_cache.get(model)
    if cached and cached[0] == mtime:
        return cached[1]
    with open(path, 'rb') as f:
        parsed = report_generator.parse_excel_data(io.BytesIO(f.read()))
    if len(_parse_cache) >= PARSE_CACHE_SIZE and model not in _parse_cache:
        _parse_cache.pop(next(iter(_parse_cache)))
    _parse_cache[model] = (mtime, parsed)
    return parsed


def _read_versions_meta(model):
    meta_path = os.path.join(_model_versions_dir(model), 'meta.json')
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (ValueError, OSError):
            pass
    return {}


def _write_versions_meta(model, meta):
    os.makedirs(_model_versions_dir(model), exist_ok=True)
    meta_path = os.path.join(_model_versions_dir(model), 'meta.json')
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _snapshot_current(model, source, note=''):
    """将当前数据包快照进 versions/，返回版本 ID；无当前数据包时返回 None"""
    src = _current_data_file(model)
    if not src:
        return None
    versions_dir = _model_versions_dir(model)
    os.makedirs(versions_dir, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    vid, seq = stamp, 1
    while os.path.exists(os.path.join(versions_dir, f'{VERSION_FILE_PREFIX}{vid}.xlsx')):
        seq += 1
        vid = f'{stamp}_{seq}'
    dst = os.path.join(versions_dir, f'{VERSION_FILE_PREFIX}{vid}.xlsx')
    shutil.copy2(src, dst)
    meta = _read_versions_meta(model)
    meta[vid] = {
        'filename': os.path.basename(src),
        'size': os.path.getsize(dst),
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'note': note or '',
        'source': source,  # auto: 变更前自动快照 / upload: 上传的新版本
    }
    _write_versions_meta(model, meta)
    logger.info('型号 %s 数据包已记录版本 %s（来源: %s）', model, vid, source)
    return vid


def _version_path(model, vid):
    """根据版本 ID 定位快照文件；vid 仅允许字母数字下划线连字符，防止路径穿越"""
    if not re.match(r'^[\w-]+$', vid or ''):
        raise ValueError('版本 ID 不合法')
    path = os.path.join(_model_versions_dir(model), f'{VERSION_FILE_PREFIX}{vid}.xlsx')
    if not os.path.isfile(path):
        raise ValueError(f'版本 {vid} 不存在')
    return path


def _list_versions(model):
    versions_dir = _model_versions_dir(model)
    if not os.path.isdir(versions_dir):
        return []
    meta = _read_versions_meta(model)
    items = []
    for name in sorted(os.listdir(versions_dir)):
        if not name.startswith(VERSION_FILE_PREFIX) or not name.lower().endswith('.xlsx'):
            continue
        vid = name[len(VERSION_FILE_PREFIX):-len('.xlsx')]
        path = os.path.join(versions_dir, name)
        info = meta.get(vid, {})
        items.append({
            'id': vid,
            'filename': name,
            'size': os.path.getsize(path),
            'time': info.get('time') or datetime.fromtimestamp(
                os.path.getmtime(path)).strftime('%Y-%m-%d %H:%M:%S'),
            'note': info.get('note', ''),
            'source': info.get('source', ''),
        })
    items.sort(key=lambda x: x['id'], reverse=True)
    return items


def _validate_xlsx_content(content):
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            if zf.testzip() is not None:
                raise ValueError('文件内容损坏')
    except zipfile.BadZipFile:
        raise ValueError('不是有效的 .xlsx 文件')


def _save_new_data_file(model, storage, note=''):
    """将上传文件保存为型号当前数据包。

    版本语义：旧当前版本先以 'auto' 快照入历史，新文件写入后同样
    以 'upload' 入历史（携带用户备注），versions/ 保存完整变更谱系。
    """
    filename = os.path.basename(storage.filename or '')
    if not filename.lower().endswith('.xlsx'):
        raise ValueError('数据包必须是 .xlsx 文件')
    content = storage.read()
    _validate_xlsx_content(content)
    _snapshot_current(model, 'auto')

    root = _model_root(model)
    for f in os.listdir(root):
        p = os.path.join(root, f)
        if os.path.isfile(p) and f.lower().endswith('.xlsx'):
            os.remove(p)
    dst = os.path.join(root, filename)
    with open(dst, 'wb') as out:
        out.write(content)
    _parse_cache.pop(model, None)

    new_vid = _snapshot_current(model, 'upload', note=note)
    logger.info('型号 %s 数据包已更新: %s（%d 字节，版本 %s）', model, filename, len(content), new_vid)
    return dst


def _model_templates(model):
    """型号模板列表（.docx/.xlsx），附带 templates.json 中配置的报告类型"""
    tdir = _model_templates_dir(model)
    templates = []
    if os.path.isdir(tdir):
        config = _read_template_config(model)
        for f in sorted(os.listdir(tdir)):
            p = os.path.join(tdir, f)
            if not os.path.isfile(p) or f.startswith('~$'):
                continue
            if not f.lower().endswith(('.docx', '.xlsx')):
                continue
            stem = os.path.splitext(f)[0]
            report_type = config.get(f) or config.get(stem) or ''
            templates.append({
                'name': f,
                'report_type': report_type,
                **_file_info(p)
            })
    return templates


# 模板报告类型配置文件（位于型号 templates/ 目录，可手工编辑或通过接口保存）
TEMPLATE_CONFIG_FILENAME = 'templates.json'


def _template_config_path(model):
    return os.path.join(_model_templates_dir(model), TEMPLATE_CONFIG_FILENAME)


def _read_template_config(model):
    path = _template_config_path(model)
    if os.path.isfile(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k).strip(): str(v).strip()
                        for k, v in data.items()
                        if str(k).strip() and str(v).strip()}
        except (ValueError, OSError):
            pass
    return {}


def _write_template_config(model, mapping):
    os.makedirs(_model_templates_dir(model), exist_ok=True)
    with open(_template_config_path(model), 'w', encoding='utf-8') as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)


def _model_summary(model):
    """型号概览信息（用于总览卡片）"""
    data_path = _current_data_file(model)
    has_data = data_path is not None
    batch_count = 0
    if has_data:
        try:
            parsed = _get_parsed_data(model)
            batch_count = len(parsed['batch_numbers']) if parsed else 0
        except ValueError as e:
            logger.warning('型号 %s 数据包解析失败: %s', model, e)
    return {
        'model': model,
        'has_data': has_data,
        'data_file': _file_info(data_path) if data_path else None,
        'batch_count': batch_count,
        'template_count': len(_model_templates(model)),
    }


@app.route('/api/database/overview', methods=['GET'])
def database_overview():
    """型号总览：一个型号目录 = 一个型号"""
    root = get_data_root()
    models = []
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            if not os.path.isdir(os.path.join(root, name)):
                continue
            if name.startswith('.') or name.startswith('~$'):
                continue
            try:
                models.append(_model_summary(name))
            except ValueError as e:
                logger.warning('跳过无法识别的目录 %s: %s', name, e)
    return jsonify({'success': True, 'data_root': os.path.basename(root), 'models': models})


@app.route('/api/database/models', methods=['POST'])
def database_create_model():
    """新建型号，可选同时上传初始数据包"""
    try:
        model = _validate_model_name(request.form.get('name', ''))
        root = os.path.join(get_data_root(), model)
        if os.path.exists(root):
            return jsonify({'error': f'型号 "{model}" 已存在'}), 400
        os.makedirs(os.path.join(root, 'templates'))
        os.makedirs(os.path.join(root, 'versions'))
        file = request.files.get('file')
        if file and file.filename:
            _save_new_data_file(model, file, note='初始上传')
        logger.info('已创建型号: %s', model)
        return jsonify({'success': True, 'model': model})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except OSError:
        logger.exception("创建型号失败")
        return jsonify({'error': '创建型号失败，请查看服务端日志'}), 500


@app.route('/api/database/models/<model>', methods=['GET'])
def database_model_detail(model):
    """型号详情：数据包信息、批次、模板列表、版本列表"""
    try:
        data_path = _current_data_file(model)
        has_data = data_path is not None
        batches, param_count = [], 0
        doc_stats = {'通用文档': 0, '单机文档': 0}
        parse_error = None
        if has_data:
            try:
                parsed = _get_parsed_data(model)
                batches = parsed['batch_numbers']
                param_count = len(parsed['parameters'])
                doc_stats = {
                    '通用文档': len(parsed['documents']['通用文档']),
                    '单机文档': sum(len(d) for d in parsed['documents']['单机文档'].values()),
                }
            except ValueError as e:
                parse_error = str(e)
        return jsonify({
            'success': True,
            'model': model,
            'has_data': has_data,
            'data_file': _file_info(data_path) if data_path else None,
            'batch_numbers': batches,
            'parameter_count': param_count,
            'document_stats': doc_stats,
            'parse_error': parse_error,
            'templates': _model_templates(model),
            'versions': _list_versions(model),
        })
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/database/models/<model>/data', methods=['POST'])
def database_upload_data(model):
    """上传新数据包（当前版本自动快照）"""
    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'error': '没有文件上传'}), 400
    try:
        _save_new_data_file(model, file, note=(request.form.get('note') or '').strip())
        return jsonify({'success': True, 'data_file': _file_info(_current_data_file(model))})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except OSError:
        logger.exception("上传数据包失败")
        return jsonify({'error': '上传数据包失败，请查看服务端日志'}), 500


@app.route('/api/database/models/<model>/templates', methods=['POST'])
def database_upload_template(model):
    """上传模板（.docx/.xlsx）到型号 templates/，同名覆盖"""
    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'error': '没有文件上传'}), 400
    try:
        name = os.path.basename(file.filename)
        if not name.lower().endswith(('.docx', '.xlsx')):
            return jsonify({'error': '模板必须是 .docx 或 .xlsx 文件'}), 400
        tdir = _model_templates_dir(model)
        os.makedirs(tdir, exist_ok=True)
        dst = os.path.join(tdir, name)
        if os.path.exists(dst):
            logger.info('型号 %s 模板覆盖: %s', model, name)
        file.save(dst)
        return jsonify({'success': True, 'template': {'name': name, **_file_info(dst)}})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except OSError:
        logger.exception("上传模板失败")
        return jsonify({'error': '上传模板失败，请查看服务端日志'}), 500


@app.route('/api/database/models/<model>/generate', methods=['POST'])
def database_generate(model):
    """按型号模板与批次号生成报告（数据直接来自型号当前数据包）"""
    data = request.json or {}
    template_filename = data.get('template_filename')
    batch_number = data.get('batch_number')
    if not template_filename or not batch_number:
        return jsonify({'error': '缺少 template_filename 或 batch_number'}), 400

    try:
        parsed = _get_parsed_data(model)
        if not parsed:
            return jsonify({'error': f'型号 "{model}" 尚无数据包，请先上传'}), 400
        if batch_number not in parsed['batch_numbers']:
            return jsonify({'error': f'批次号 "{batch_number}" 不存在'}), 400

        template_path = os.path.join(_model_templates_dir(model), os.path.basename(template_filename))
        if not os.path.isfile(template_path):
            return jsonify({'error': f'模板 "{template_filename}" 不存在'}), 404
        if not template_filename.lower().endswith(('.docx', '.xlsx')):
            return jsonify({'error': '模板必须是 .docx 或 .xlsx 文件'}), 400

        template_kind = get_template_kind(template_filename)
        output_ext = '.xlsx' if template_kind == 'excel' else '.docx'
        template_stem = os.path.splitext(template_filename)[0]
        resolved_name = report_generator.get_document_name(
            parsed['documents'], template_stem, batch_number
        )
        output_filename = build_safe_output_filename(resolved_name, batch_number, output_ext)

        output_stream = io.BytesIO()
        success, param_count, doc_count, error_msg = report_generator.generate_report(
            template_path, batch_number, parsed['parameters'], parsed['documents'],
            output_stream, template_filename
        )
        if not success:
            return jsonify({'error': error_msg or '报告生成失败'}), 500

        report_base64 = base64.b64encode(output_stream.getvalue()).decode('utf-8')
        return jsonify({
            'success': True,
            'output_filename': output_filename,
            'file_type': template_kind,
            'report_content': report_base64,
            'statistics': {
                'batch_number': batch_number,
                'parameter_replacements': param_count,
                'document_replacements': doc_count,
                'total_replacements': param_count + doc_count
            }
        })
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception:
        logger.exception("生成报告时出错")
        return jsonify({'error': '生成报告时出错，请查看服务端日志'}), 500


@app.route('/api/database/models/<model>/templates/config', methods=['POST'])
def database_template_config(model):
    """保存模板报告类型映射（写入 templates/templates.json）"""
    data = request.json or {}
    types = data.get('types')
    if not isinstance(types, dict):
        return jsonify({'error': '缺少 types 映射'}), 400
    try:
        mapping = {}
        for k, v in types.items():
            k, v = str(k).strip(), str(v).strip()
            if k and v:
                mapping[k] = v
        existing = {t['name'] for t in _model_templates(model)}
        existing_stems = {os.path.splitext(n)[0] for n in existing}
        unknown = [k for k in mapping if k not in existing and k not in existing_stems]
        if unknown:
            return jsonify({'error': f'以下键没有对应的模板文件: {", ".join(unknown)}'}), 400
        _write_template_config(model, mapping)
        logger.info('型号 %s 模板报告类型已更新（%d 项）', model, len(mapping))
        return jsonify({'success': True, 'types': mapping})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except OSError:
        logger.exception("保存模板报告类型失败")
        return jsonify({'error': '保存模板报告类型失败，请查看服务端日志'}), 500


@app.route('/api/database/models/<model>/data/update', methods=['POST'])
def database_update_data(model):
    """将预览表格中的单元格编辑写回当前数据包（写回前当前版本自动快照）"""
    data = request.json or {}
    edits = data.get('edits')
    note = (data.get('note') or '').strip()
    if not isinstance(edits, list) or not edits:
        return jsonify({'error': '缺少编辑内容'}), 400

    clean_edits = []
    for e in edits:
        if not isinstance(e, dict):
            return jsonify({'error': '编辑内容格式不正确'}), 400
        pid = str(e.get('param_id', '')).strip()
        field = str(e.get('field', '')).strip()
        if not pid or field not in ('value', 'description'):
            return jsonify({'error': f'编辑内容不合法: {pid or "?"} {field or "?"}'}), 400
        if field == 'value' and not str(e.get('batch', '')).strip():
            return jsonify({'error': f'值编辑缺少批次号: {pid}'}), 400
        clean_edits.append({
            'param_id': pid,
            'field': field,
            'batch': str(e.get('batch', '')).strip(),
            'value': e.get('value', ''),
        })

    try:
        path = _current_data_file(model)
        if not path:
            return jsonify({'error': f'型号 "{model}" 尚无数据包，请先上传'}), 400

        with open(path, 'rb') as f:
            content = f.read()

        out_stream = io.BytesIO()
        updated, missing, err = report_generator.update_data_cells(
            io.BytesIO(content), clean_edits, out_stream
        )
        if err:
            return jsonify({'error': err}), 400
        if updated == 0:
            return jsonify({
                'error': '没有可写回的编辑（参数/批次未在数据包中定位到）',
                'missing': missing
            }), 400

        # 版本谱系：写回前旧版本自动快照，写回后的新内容同样入历史
        _snapshot_current(model, 'auto')
        with open(path, 'wb') as f:
            f.write(out_stream.getvalue())
        _parse_cache.pop(model, None)
        _snapshot_current(model, 'edit', note=note or '表格编辑保存')

        logger.info('型号 %s 数据包单元格编辑已写回: %d 处', model, updated)
        return jsonify({
            'success': True,
            'updated': updated,
            'missing': missing,
            'data_file': _file_info(path)
        })
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except OSError:
        logger.exception("写回数据包失败")
        return jsonify({'error': '写回数据包失败，请查看服务端日志'}), 500


@app.route('/api/database/models/<model>/generate_all', methods=['POST'])
def database_generate_all(model):
    """按批次号对该型号的全部模板一键生成报告，打包为 zip 返回"""
    data = request.json or {}
    batch_number = data.get('batch_number')
    if not batch_number:
        return jsonify({'error': '缺少 batch_number'}), 400

    try:
        parsed = _get_parsed_data(model)
        if not parsed:
            return jsonify({'error': f'型号 "{model}" 尚无数据包，请先上传'}), 400
        if batch_number not in parsed['batch_numbers']:
            return jsonify({'error': f'批次号 "{batch_number}" 不存在'}), 400

        templates = _model_templates(model)
        if not templates:
            return jsonify({'error': f'型号 "{model}" 暂无模板，请先上传'}), 400

        zip_buf = io.BytesIO()
        results = []
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for t in templates:
                template_path = os.path.join(_model_templates_dir(model), t['name'])
                template_kind = get_template_kind(t['name'])
                output_ext = '.xlsx' if template_kind == 'excel' else '.docx'
                template_stem = os.path.splitext(t['name'])[0]
                resolved_name = report_generator.get_document_name(
                    parsed['documents'], template_stem, batch_number
                )
                output_name = build_safe_output_filename(resolved_name, batch_number, output_ext)

                output_stream = io.BytesIO()
                success, param_count, doc_count, error_msg = report_generator.generate_report(
                    template_path, batch_number, parsed['parameters'], parsed['documents'],
                    output_stream, t['name']
                )
                if not success:
                    results.append({
                        'template': t['name'], 'success': False, 'error': error_msg or '生成失败'
                    })
                    continue

                # zip 内文件名去重（不同模板可能解析出相同的文档名）
                arcname, seq = output_name, 1
                stem, ext = os.path.splitext(output_name)
                while arcname in zf.namelist():
                    seq += 1
                    arcname = f'{stem}_{seq}{ext}'
                zf.writestr(arcname, output_stream.getvalue())
                results.append({
                    'template': t['name'], 'success': True,
                    'output_filename': output_name,
                    'parameter_replacements': param_count,
                    'document_replacements': doc_count,
                })

        ok_count = sum(1 for r in results if r['success'])
        zip_buf.seek(0)
        return jsonify({
            'success': ok_count > 0,
            'zip_filename': f'{model}_{batch_number}_报告合集_{stamp}.zip',
            'report_content': base64.b64encode(zip_buf.getvalue()).decode('utf-8'),
            'results': results,
            'statistics': {
                'total_templates': len(templates),
                'success_count': ok_count,
                'failed_count': len(results) - ok_count,
            }
        })
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception:
        logger.exception("一键生成全部报告时出错")
        return jsonify({'error': '一键生成全部报告时出错，请查看服务端日志'}), 500


@app.route('/api/database/models/<model>/parameters', methods=['GET'])
def database_model_parameters(model):
    """型号全部批次与参数（供一致性对比页），形状与 /api/consistency/parameters 相同"""
    try:
        parsed = _get_parsed_data(model)
        if not parsed:
            return jsonify({'error': f'型号 "{model}" 尚无数据包，请先上传'}), 400
        return jsonify({
            'success': True,
            'batch_numbers': parsed['batch_numbers'],
            'parameters': [
                {'id': p['id'], 'description': p['description'], 'values': p['data']}
                for p in parsed['parameters']
            ]
        })
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/database/models/<model>/versions/<vid>/download', methods=['GET'])
def database_version_download(model, vid):
    """下载指定版本的数据包"""
    try:
        path = _version_path(model, vid)
        return send_file(path, as_attachment=True, download_name=f'{model}_数据包_{vid}.xlsx')
    except ValueError as e:
        return jsonify({'error': str(e)}), 404


@app.route('/api/database/models/<model>/versions/<vid>/restore', methods=['POST'])
def database_version_restore(model, vid):
    """恢复指定版本为当前数据包（恢复前当前版本自动快照）"""
    try:
        src = _version_path(model, vid)
        _snapshot_current(model, 'restore')
        root = _model_root(model)
        for f in os.listdir(root):
            p = os.path.join(root, f)
            if os.path.isfile(p) and f.lower().endswith('.xlsx'):
                os.remove(p)
        shutil.copy2(src, os.path.join(root, os.path.basename(src)))
        _parse_cache.pop(model, None)
        logger.info('型号 %s 已恢复版本 %s', model, vid)
        return jsonify({'success': True})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except OSError:
        logger.exception("恢复版本失败")
        return jsonify({'error': '恢复版本失败，请查看服务端日志'}), 500


@app.route('/api/database/models/<model>/versions/<vid>', methods=['DELETE'])
def database_version_delete(model, vid):
    """删除指定版本快照"""
    try:
        path = _version_path(model, vid)
        os.remove(path)
        meta = _read_versions_meta(model)
        meta.pop(vid, None)
        _write_versions_meta(model, meta)
        logger.info('型号 %s 已删除版本 %s', model, vid)
        return jsonify({'success': True})
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except OSError:
        logger.exception("删除版本失败")
        return jsonify({'error': '删除版本失败，请查看服务端日志'}), 500


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
