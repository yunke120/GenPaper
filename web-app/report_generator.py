#!/usr/bin/env python
"""
单机数据包报告生成器核心模块
支持 Word (.docx) 模板（docxtpl 参数替换 + 文本框兜底处理）
和 Excel (.xlsx) 模板（遍历所有 sheet 的单元格替换占位符）
"""

import re
import os
import io
import logging
import zipfile
from datetime import datetime

import pandas as pd

logger = logging.getLogger(__name__)

# 兜底 XML 清洗逻辑（_normalize_xml_for_jinja）与 docxtpl 的 patch_xml/render_xml_part
# 内部实现对应，docxtpl 大版本升级时需复核此处是否仍然一致。


def _is_valid_jinja_identifier(name):
    """判断参数 ID 能否直接用作 Word 模板的 Jinja2 变量名（Excel 模板路径无此限制）"""
    try:
        return str(name).isidentifier()
    except Exception:
        return False


def parse_excel_data(excel_file):
    """
    解析Excel文件，提取参数数据和文档数据
    支持文件路径或文件对象

    Args:
        excel_file: Excel文件路径或文件对象

    Returns:
        dict: 包含批次号、参数和文档数据的字典
    """
    try:
        xl = pd.ExcelFile(excel_file)

        # 识别sheet名称：优先精确匹配，其次包含匹配
        def _find_sheet(keyword):
            for sheet in xl.sheet_names:
                if sheet.strip() == keyword:
                    return sheet
            for sheet in xl.sheet_names:
                if keyword in sheet:
                    return sheet
            return None

        data_sheet = _find_sheet('数据')
        doc_sheet = _find_sheet('文档')

        if not data_sheet:
            raise ValueError(f"在Excel文件中未找到'数据'sheet")

        logger.info("找到数据sheet: %s", data_sheet)
        if doc_sheet:
            logger.info("找到文档sheet: %s", doc_sheet)

        # 解析数据sheet
        df_data = xl.parse(data_sheet, header=None)

        # 提取批次号（从第0行，第3列开始），同时记录列号，参数取值按实际列对应，
        # 不假设批次列连续，避免出现列错位
        batch_columns = []
        for col in range(3, df_data.shape[1]):
            batch = df_data.iat[0, col]
            if pd.notna(batch):
                batch_str = str(batch).strip()
                if batch_str:
                    batch_columns.append((batch_str, col))

        batch_numbers = [b for b, _ in batch_columns]
        for expected_index, (batch_str, col) in enumerate(batch_columns):
            expected_col = 3 + expected_index
            if col != expected_col:
                logger.warning(
                    "数据sheet 批次列不连续（'%s' 位于第 %d 列，预期第 %d 列），"
                    "参数取值已按实际列号对应", batch_str, col + 1, expected_col + 1
                )

        logger.info("发现批次号: %s", batch_numbers)

        # 提取参数信息（从第1行开始）
        parameters = []
        for row in range(1, df_data.shape[0]):
            param_id = df_data.iat[row, 1]
            description = df_data.iat[row, 2]

            if pd.notna(param_id) and str(param_id).strip() != 'ID':
                param_id_str = str(param_id).strip()
                param_info = {
                    'id': param_id_str,
                    'description': str(description).strip() if pd.notna(description) else '',
                    'data': {}
                }

                for batch_str, col_idx in batch_columns:
                    if col_idx < df_data.shape[1]:
                        value = df_data.iat[row, col_idx]
                        param_info['data'][batch_str] = str(value).strip() if pd.notna(value) else ''

                parameters.append(param_info)

                # Word 模板路径以参数 ID 作为 Jinja2 变量名，提前给出约束警告
                if not _is_valid_jinja_identifier(param_id_str):
                    logger.warning(
                        "参数 ID '%s' 不是合法标识符，Word 模板中将无法通过 {{%s}} 引用"
                        "（Excel 模板不受影响）", param_id_str, param_id_str
                    )

        logger.info("提取到 %d 个参数", len(parameters))

        # 解析文档sheet
        document_data = {
            '通用文档': {},
            '单机文档': {},
            # 单机文档批次组下实际出现的文档 ID 前缀，供 get_document_value 动态判断
            '单机文档前缀': set()
        }

        if doc_sheet:
            df_doc = xl.parse(doc_sheet, header=None)

            group_mappings = {}

            if df_doc.shape[0] > 1:
                col = 0
                while col < df_doc.shape[1]:
                    if col + 2 < df_doc.shape[1]:
                        col_header = str(df_doc.iat[1, col]).strip() if pd.notna(df_doc.iat[1, col]) else ''
                        if col_header == 'ID':
                            if pd.notna(df_doc.iat[0, col]):
                                group_name = str(df_doc.iat[0, col]).strip()

                                if re.match(r'^[A-Za-z][A-Za-z0-9]*-\d+$', group_name):
                                    if '单机文档' not in group_mappings:
                                        group_mappings['单机文档'] = {'columns': [], 'is_batch': True}
                                    group_mappings['单机文档']['columns'].append(col)
                                else:
                                    if group_name not in group_mappings:
                                        group_mappings[group_name] = {'columns': [col], 'is_batch': False}
                                    else:
                                        group_mappings[group_name]['columns'].append(col)
                            col += 3
                        else:
                            col += 1
                    else:
                        col += 1

            logger.info("自动识别到文档分组: %s", list(group_mappings.keys()))

            # 解析通用文档分组
            for group_name, group_info in group_mappings.items():
                if group_name == '单机文档' or group_info.get('is_batch'):
                    continue

                if group_info['columns']:
                    start_col = group_info['columns'][0]
                    logger.info("%s数据位于列 %d-%d", group_name, start_col, start_col + 2)

                    for row in range(2, df_doc.shape[0]):
                        doc_id = df_doc.iat[row, start_col]
                        if pd.notna(doc_id):
                            doc_id_str = str(doc_id).strip()
                            number = df_doc.iat[row, start_col + 1]
                            name = df_doc.iat[row, start_col + 2]

                            if pd.notna(number) and pd.notna(name):
                                document_data['通用文档'][doc_id_str] = {
                                    'number': str(number).strip(),
                                    'name': str(name).strip()
                                }

            # 解析单机文档
            if '单机文档' in group_mappings:
                batch_columns_doc = group_mappings['单机文档']['columns']
                batch_names = []

                for col in batch_columns_doc:
                    if pd.notna(df_doc.iat[0, col]):
                        batch_names.append(str(df_doc.iat[0, col]).strip())
                    else:
                        batch_names.append(f"批次{col}")

                logger.info("识别到 %d 个单机文档批次: %s", len(batch_columns_doc), batch_names)

                for batch_idx, batch_start_col in enumerate(batch_columns_doc):
                    if batch_idx < len(batch_names):
                        batch = batch_names[batch_idx]
                        document_data['单机文档'][batch] = {}

                        for row in range(2, df_doc.shape[0]):
                            doc_id = df_doc.iat[row, batch_start_col]
                            if pd.notna(doc_id):
                                doc_id_str = str(doc_id).strip()
                                number = df_doc.iat[row, batch_start_col + 1]
                                name = df_doc.iat[row, batch_start_col + 2]

                                if pd.notna(number) and pd.notna(name):
                                    document_data['单机文档'][batch][doc_id_str] = {
                                        'number': str(number).strip(),
                                        'name': str(name).strip()
                                    }

            # 收集批次组下的文档 ID 前缀（如 Doc、Fxt），替代旧的硬编码 'Doc' 判断
            for docs in document_data['单机文档'].values():
                for key in docs:
                    if '_' in key:
                        document_data['单机文档前缀'].add(key.rsplit('_', 1)[0])

            logger.info("文档数据: %d个通用文档, %d个单机文档",
                        len(document_data['通用文档']),
                        sum(len(docs) for docs in document_data['单机文档'].values()))

        return {
            'batch_numbers': batch_numbers,
            'parameters': parameters,
            'documents': document_data,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

    except Exception as e:
        raise ValueError(f"解析Excel文件失败: {e}")


def get_doc_type_prefixes(doc_data):
    """从文档数据中动态提取所有文档类型前缀"""
    doc_types = set()

    for key in doc_data.get('通用文档', {}).keys():
        if '_' in key:
            prefix = key.rsplit('_', 1)[0]
            doc_types.add(prefix)

    for batch_docs in doc_data.get('单机文档', {}).values():
        for key in batch_docs.keys():
            if '_' in key:
                prefix = key.rsplit('_', 1)[0]
                doc_types.add(prefix)

    return doc_types


def get_document_value(doc_data, placeholder, batch):
    """根据占位符获取文档值，格式为: number《name》

    占位符前缀属于批次组（单机文档）时按批次查询，否则按通用文档查询；
    批次中不存在时回退到通用文档，两个来源互相兜底。
    """
    doc_types = get_doc_type_prefixes(doc_data)

    if not doc_types:
        return None

    escaped_types = '|'.join(re.escape(t) for t in sorted(doc_types, key=len, reverse=True))
    pattern = rf'^({escaped_types})_(\d+)$'

    match = re.match(pattern, placeholder)
    if not match:
        return None

    doc_type = match.group(1)
    doc_id = match.group(2)
    full_id = f"{doc_type}_{doc_id}"

    # 批次组前缀从解析结果动态获取，兼容无该元数据时沿用旧的 'Doc' 约定
    batch_prefixes = doc_data.get('单机文档前缀') or {'Doc'}

    if doc_type in batch_prefixes:
        batch_docs = doc_data.get('单机文档', {}).get(batch)
        if batch_docs and full_id in batch_docs:
            item = batch_docs[full_id]
            return f"{item['number']}《{item['name']}》"

    if full_id in doc_data.get('通用文档', {}):
        item = doc_data['通用文档'][full_id]
        return f"{item['number']}《{item['name']}》"

    return None


def get_document_name(doc_data, placeholder, batch):
    """根据批次和文档占位符获取报告名称，用于生成报告保存文件名。"""
    value = get_document_value(doc_data, placeholder, batch)
    if not value:
        return None
    match = re.search(r'《(.+)》', value)
    return match.group(1) if match else value


EXCEL_TEMPLATE_EXTENSIONS = {'.xlsx', '.xls'}


def get_template_kind(template_filename):
    """根据模板文件名判断模板类型: excel 或 docx"""
    if template_filename:
        ext = os.path.splitext(str(template_filename))[1].lower()
        if ext in EXCEL_TEMPLATE_EXTENSIONS:
            return 'excel'
    return 'docx'


_PLACEHOLDER_RE = re.compile(r'\{\{([^{}]+)\}\}')

_DOC_CALL_RE = re.compile(r"^doc\(\s*['\"]([^'\"]+)['\"]\s*\)$")


def xml_escape_text(value):
    """将上下文值转义为可安全嵌入 Word XML 文本节点的内容。

    Word 模板的两条渲染路径（docxtpl 主路径、ZIP 兜底路径）默认都不转义
    （docxtpl 0.20.2 render(autoescape=False)），因此统一在构建上下文时转义。
    """
    if not isinstance(value, str):
        value = str(value)
    return value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _convert_newlines_in_text_nodes(xml_str):
    """将 <w:t> 文本节点内的换行符转换为 <w:br/>。

    与 docxtpl resolve_listing 的行为保持一致，使兜底替换路径与
    docxtpl 主路径的多行值渲染结果相同。
    """
    def _sub(m):
        inner = m.group(2).replace('\n', '</w:t><w:br/><w:t xml:space="preserve">')
        return f"{m.group(1)}{inner}{m.group(3)}"

    return re.sub(r'(<w:t(?: [^>]*)?>)(.*?)(</w:t>)', _sub, xml_str, flags=re.DOTALL)


def _replace_excel_cell(value, context, doc_data, batch):
    """
    替换单元格文本中的所有占位符。
    返回 (替换后的文本, 参数替换次数, 文档替换次数)。
    """
    param_count = 0
    doc_count = 0

    def replace_one(match):
        nonlocal param_count, doc_count
        name = match.group(1).strip()
        if name in context:
            param_count += 1
            return context[name]
        doc_value = get_document_value(doc_data, name, batch)
        if doc_value is not None:
            doc_count += 1
            return doc_value
        logger.warning("未找到数据或文档 {{{{%s}}}}", name)
        return match.group(0)

    return _PLACEHOLDER_RE.sub(replace_one, value), param_count, doc_count


def _warn_excel_template_objects(template_file):
    """检测 Excel 模板中的图表/图片对象：openpyxl 保存后这些对象会丢失"""
    try:
        if hasattr(template_file, 'seek'):
            template_file.seek(0)
        with zipfile.ZipFile(template_file) as zf:
            lost = [n for n in zf.namelist()
                    if n.startswith(('xl/charts/', 'xl/media/', 'xl/drawings/'))]
        if lost:
            logger.warning(
                "Excel 模板包含 %d 个图表/图片/绘图对象，openpyxl 保存后这些对象会丢失: %s",
                len(lost), lost[:10]
            )
    except (zipfile.BadZipFile, ValueError, OSError):
        pass
    finally:
        if hasattr(template_file, 'seek'):
            template_file.seek(0)


def generate_excel_report(template_file, batch, param_data, doc_data, output_file,
                          template_filename=None):
    """
    使用 .xlsx 模板生成数据报告：保留原工作簿结构与样式，
    仅替换单元格文本中的占位符。

    Returns:
        tuple: (success, param_replacements, doc_replacements, error)
    """
    try:
        from openpyxl import load_workbook

        ext = os.path.splitext(str(template_filename))[1].lower() if template_filename else ''
        if ext == '.xls':
            raise ValueError('为保留原结构样式，Excel 模板请使用 .xlsx 格式，不支持直接编辑 .xls')

        _warn_excel_template_objects(template_file)

        context = {}
        for param in param_data:
            context[param['id']] = param['data'].get(batch, f'[数据缺失: {batch}]')

        wb = load_workbook(template_file, data_only=False)

        param_replacements = 0
        doc_replacements = 0
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    value = cell.value
                    if not isinstance(value, str) or '{{' not in value:
                        continue
                    if cell.data_type == 'f':
                        # 公式单元格仅保留原样，避免替换内容破坏公式结构
                        continue
                    new_value, p_count, d_count = _replace_excel_cell(
                        value, context, doc_data, batch
                    )
                    param_replacements += p_count
                    doc_replacements += d_count
                    if new_value != value:
                        cell.value = new_value

        wb.save(output_file)
        if hasattr(output_file, 'seek'):
            output_file.seek(0)

        logger.info("Excel 模板完成替换: %d个参数, %d个文档", param_replacements, doc_replacements)
        return True, param_replacements, doc_replacements, None
    except Exception as e:
        logger.exception("Excel 模板生成失败")
        return False, 0, 0, str(e)


def generate_consistency_report(param_data, batch_numbers, parameter_ids, output_file):
    """
    生成数据一致性对比 Word 报告。
    按选中的参数与批次，在表格中列出各批次数值，并给出是否一致的结论。

    判定口径：字符串精确比较（strip 后）；空值不参与判定；
    数值格式差异（如 1.0 与 1.00）会被判为不一致。

    Returns:
        tuple: (success, param_count, inconsistent_count, error)
    """
    try:
        from docx import Document
        from docx.shared import Pt
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        selected_batches = [b for b in batch_numbers if b]
        selected_ids = {str(pid).strip() for pid in parameter_ids if str(pid).strip()}
        params = [p for p in param_data if p['id'] in selected_ids]

        doc = Document()

        title = doc.add_paragraph()
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = title.add_run('数据一致性对比')
        run.bold = True
        run.font.size = Pt(16)

        doc.add_paragraph(f"对比批次：{'、'.join(selected_batches)}")
        doc.add_paragraph(f"对比参数：{len(params)} 项")

        table = doc.add_table(rows=1, cols=4 + len(selected_batches))
        table.style = 'Table Grid'

        headers = ['序号', '参数ID', 'Description'] + selected_batches + ['一致性']
        for idx, header in enumerate(headers):
            cell = table.rows[0].cells[idx]
            cell.text = header
            for paragraph in cell.paragraphs:
                for r in paragraph.runs:
                    r.bold = True
                    r.font.size = Pt(9)

        inconsistent_count = 0
        missing_values = 0
        for row_idx, param in enumerate(params, start=1):
            row = table.add_row()
            values = []
            row.cells[0].text = str(row_idx)
            row.cells[1].text = param['id']
            row.cells[2].text = param['description']
            for col_idx, batch in enumerate(selected_batches, start=3):
                value = param['data'].get(batch, '')
                row.cells[col_idx].text = str(value)
                values.append(str(value))
            normalized = {v.strip() for v in values if str(v).strip()}
            missing_values += len(values) - len(normalized)
            consistent = len(normalized) <= 1
            if not consistent:
                inconsistent_count += 1
            row.cells[3 + len(selected_batches)].text = '一致' if consistent else '不一致'
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    for r in paragraph.runs:
                        r.font.size = Pt(9)

        summary_text = (
            f"共对比 {len(params)} 个参数、{len(selected_batches)} 个批次；"
            f"一致 {len(params) - inconsistent_count} 项，不一致 {inconsistent_count} 项。"
        )
        if missing_values:
            summary_text += f"其中 {missing_values} 个对比值为空（空值不参与一致性判定）。"
        summary = doc.add_paragraph()
        summary.add_run(summary_text)

        doc.save(output_file)
        if hasattr(output_file, 'seek'):
            output_file.seek(0)

        logger.info("一致性报告完成: %d个参数, %d个批次, %d项不一致",
                    len(params), len(selected_batches), inconsistent_count)
        return True, len(params), inconsistent_count, None
    except Exception as e:
        logger.exception("一致性报告生成失败")
        return False, 0, 0, str(e)


def _normalize_xml_for_jinja(xml_str):
    """
    清理 Word XML 中的跨 run 标签，使 Jinja2 能正确识别占位符。
    复用 docxtpl 的 patch_xml 逻辑（与 docxtpl 0.20.x 对应，升级时需复核）。
    """
    # replace {<something>{ by {{
    xml_str = re.sub(
        r"(?<={)(<[^>]*>)+(?=[\{%\#])|(?<=[%\}\#])(<[^>]*>)+(?=\})",
        "",
        xml_str,
        flags=re.DOTALL,
    )

    # strip tags inside {{ }} and {% %}
    def striptags(m):
        return re.sub(
            "</w:t>.*?(<w:t>|<w:t [^>]*>)", "", m.group(0), flags=re.DOTALL
        )

    xml_str = re.sub(
        r"{%(?:(?!%}).)*|{#(?:(?!#}).)*|{{(?:(?!}}).)*",
        striptags,
        xml_str,
        flags=re.DOTALL,
    )

    return xml_str


def _replace_in_xml_part(xml_str, context):
    """
    在一段 XML 字符串中，用 Jinja2 替换所有占位符。
    返回替换后的 XML 字符串。

    与 docxtpl.render_xml_part 行为保持一致：
    - 使用裸 Jinja2 渲染（docxtpl 0.20.2 同样默认不开启 autoescape）；
    - 渲染后将 <w:t> 内的换行转换为 <w:br/>。
    context 中的字符串值应当已通过 xml_escape_text 转义。
    """
    from jinja2 import Template

    normalized = _normalize_xml_for_jinja(xml_str)
    template = Template(normalized)
    result = template.render(context)
    # 恢复防解析的花括号（与 docxtpl 渲染后处理一致）
    result = result.replace("{_{", "{{").replace("}_}", "}}")
    result = result.replace("{_%", "{%").replace("%_}", "%}")
    return _convert_newlines_in_text_nodes(result)


def _count_template_placeholders(template_parts, param_ids, doc_data, batch):
    """
    统计模板各 XML 部件中占位符的真实替换数。

    Returns:
        tuple: (param_hits, doc_hits, missing_names)
    """

    def _count_part(xml_str):
        param_hits = 0
        doc_hits = 0
        missing = []
        for m in _PLACEHOLDER_RE.finditer(_normalize_xml_for_jinja(xml_str)):
            name = m.group(1).strip()
            if name in param_ids:
                param_hits += 1
                continue
            doc_call = _DOC_CALL_RE.match(name)
            lookup_name = doc_call.group(1) if doc_call else name
            if get_document_value(doc_data, lookup_name, batch) is not None:
                doc_hits += 1
            else:
                missing.append(name)
        return param_hits, doc_hits, missing

    param_hits = 0
    doc_hits = 0
    all_missing = []
    for part_xml in template_parts.values():
        p, d, missing = _count_part(part_xml)
        param_hits += p
        doc_hits += d
        all_missing.extend(missing)

    return param_hits, doc_hits, all_missing


def generate_report(template_file, batch, param_data, doc_data, output_file,
                    template_filename=None):
    """
    生成数据报告。
    Excel 模板会遍历所有 sheet 的单元格替换占位符；
    Word 模板采用双层替换策略：
    1. 先用 docxtpl 处理主体内容（处理 doc() 函数等 Jinja2 高级特性）
    2. 再直接从 ZIP 层面确保文本框中的占位符被替换
       （防止旧版 python-docx 丢失 mc:AlternateContent 中的文本框内容）

    两条 Word 路径共用同一份上下文，字符串值在构建时统一经过
    xml_escape_text 转义，避免值中的 XML 特殊字符破坏文档结构。

    Args:
        template_file: Word/Excel 模板文件路径或文件对象
        batch: 批次号
        param_data: 参数数据列表
        doc_data: 文档数据字典
        output_file: 输出文件路径或文件对象
        template_filename: 模板文件名，用于判断模板类型

    Returns:
        tuple: (success, param_replacements, doc_replacements, error)
    """
    logger.info("生成报告: %s", batch)

    if hasattr(output_file, 'write'):
        logger.info("输出到: 内存缓冲区")
    else:
        logger.info("输出到: %s", output_file)

    if get_template_kind(template_filename) == 'excel':
        return generate_excel_report(
            template_file, batch, param_data, doc_data, output_file, template_filename
        )

    try:
        from docxtpl import DocxTemplate

        # 构建上下文：值统一做 XML 转义，供 docxtpl 主路径与 ZIP 兜底路径共用
        context = {}

        for param in param_data:
            value = param['data'].get(batch, f'[数据缺失: {batch}]')
            context[param['id']] = xml_escape_text(value)

        # 文档占位符直接加入上下文，兼容 Word 模板中的 {{Fxt_4}}、{{Sj_12}}、{{Doc_3}} 形式
        for doc_key in doc_data.get('通用文档', {}):
            doc_value = get_document_value(doc_data, doc_key, batch)
            if doc_value:
                context[doc_key] = xml_escape_text(doc_value)
        for doc_key in doc_data.get('单机文档', {}).get(batch, {}):
            doc_value = get_document_value(doc_data, doc_key, batch)
            if doc_value:
                context[doc_key] = xml_escape_text(doc_value)

        def doc_lookup(ph):
            value = get_document_value(doc_data, ph, batch)
            if value:
                return xml_escape_text(value)
            logger.warning("未找到文档数据 {{{{%s}}}}", ph)
            return f'[文档缺失: {ph}]'

        context['doc'] = doc_lookup

        # ============ 步骤1: docxtpl 主体替换 ============
        doc = DocxTemplate(template_file)
        doc.render(context)
        doc.save(output_file)

        # ============ 步骤2: 检测并修复文本框中的占位符 ============
        # 读取 docxtpl 输出
        if hasattr(output_file, 'read'):
            output_file.seek(0)
            output_bytes = output_file.read()
        else:
            with open(output_file, 'rb') as f:
                output_bytes = f.read()

        # 读取原始模板（用于文本框数量对比、占位符统计与兜底重建）
        if hasattr(template_file, 'read'):
            template_file.seek(0)
            template_bytes = template_file.read()
        else:
            with open(template_file, 'rb') as f:
                template_bytes = f.read()

        # 读取模板中的 XML 部件（正文 + 页眉页脚 + 脚注尾注）
        template_parts = {}
        with zipfile.ZipFile(io.BytesIO(template_bytes)) as src_zip:
            template_parts['word/document.xml'] = src_zip.read('word/document.xml').decode('utf-8')
            for name in src_zip.namelist():
                if name.startswith(('word/header', 'word/footer',
                                    'word/footnotes', 'word/endnotes')):
                    template_parts[name] = src_zip.read(name).decode('utf-8')

        src_doc_xml = template_parts['word/document.xml']
        src_tbx_count = len(re.findall(r'<wps:txbx>', src_doc_xml))

        # 统计输出中的文本框数量并检查是否有残留占位符
        with zipfile.ZipFile(io.BytesIO(output_bytes)) as out_zip:
            out_doc_xml = out_zip.read('word/document.xml').decode('utf-8')
        out_tbx_count = len(re.findall(r'<wps:txbx>', out_doc_xml))

        need_fix = False
        fix_reason = ""

        if src_tbx_count > 0 and out_tbx_count < src_tbx_count:
            # 文本框被 python-docx 丢弃了
            need_fix = True
            fix_reason = f"文本框丢失（原始{src_tbx_count}个，输出{out_tbx_count}个）"
        elif out_tbx_count > 0:
            # 文本框存在，检查是否还有未替换的占位符
            for match in re.finditer(r'<wps:txbx>.*?</wps:txbx>', out_doc_xml, re.DOTALL):
                if '{{' in match.group(0):
                    need_fix = True
                    fix_reason = "文本框中存在未替换的占位符"
                    break
            if not need_fix:
                for match in re.finditer(r'<mc:Fallback>.*?</mc:Fallback>', out_doc_xml, re.DOTALL):
                    if '{{' in match.group(0):
                        need_fix = True
                        fix_reason = "文本框 Fallback 中存在未替换的占位符"
                        break

        if need_fix:
            logger.info("检测到文本框问题: %s，执行直接 XML 替换", fix_reason)

            # 从原始模板直接做 ZIP/XML 替换（context 值已转义，两条路径行为一致）
            replaced_parts = {
                'word/document.xml': _replace_in_xml_part(src_doc_xml, context)
            }
            for name, part_xml in template_parts.items():
                if name != 'word/document.xml':
                    replaced_parts[name] = _replace_in_xml_part(part_xml, context)

            # 重建输出 ZIP，用替换后的 XML 覆盖
            final_buf = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(output_bytes)) as out_zip:
                with zipfile.ZipFile(final_buf, 'w', zipfile.ZIP_DEFLATED) as final_zip:
                    for item in out_zip.infolist():
                        data = out_zip.read(item.filename)
                        if item.filename in replaced_parts:
                            data = replaced_parts[item.filename].encode('utf-8')
                        final_zip.writestr(item, data)

            final_buf.seek(0)
            if hasattr(output_file, 'write'):
                output_file.seek(0)
                output_file.truncate()
                output_file.write(final_buf.read())
                output_file.seek(0)
            else:
                with open(output_file, 'wb') as f:
                    f.write(final_buf.read())

        # ============ 步骤3: 统计模板占位符的真实替换数 ============
        param_ids = {p['id'] for p in param_data}
        param_replacements, doc_replacements, missing = _count_template_placeholders(
            template_parts, param_ids, doc_data, batch
        )
        if missing:
            logger.warning("模板中 %d 个占位符未匹配到数据: %s",
                           len(missing), sorted(set(missing)))

        logger.info("完成替换: %d个参数, %d个文档", param_replacements, doc_replacements)
        return True, param_replacements, doc_replacements, None

    except Exception as e:
        logger.exception("Word 报告生成失败")
        return False, 0, 0, str(e)
