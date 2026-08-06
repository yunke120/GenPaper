#!/usr/bin/env python
"""
单机数据包报告生成器核心模块
支持 Word (.docx) 模板（docxtpl 参数替换 + 文本框兜底处理）
和 Excel (.xlsx/.xls) 模板（遍历所有 sheet 的单元格替换占位符）
"""

import re
import os
import io
import zipfile
from datetime import datetime

import pandas as pd


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

        # 识别sheet名称
        data_sheet = None
        doc_sheet = None

        for sheet in xl.sheet_names:
            if '数据' in sheet or sheet.strip() == '数据':
                data_sheet = sheet
            elif '文档' in sheet or sheet.strip() == '文档':
                doc_sheet = sheet

        if not data_sheet:
            raise ValueError(f"在Excel文件中未找到'数据'sheet")

        print(f"找到数据sheet: {data_sheet}")
        if doc_sheet:
            print(f"找到文档sheet: {doc_sheet}")

        # 解析数据sheet
        df_data = xl.parse(data_sheet, header=None)

        # 提取批次号（从第0行，第3列开始）
        batch_numbers = []
        for col in range(3, df_data.shape[1]):
            batch = df_data.iat[0, col]
            if pd.notna(batch):
                batch_str = str(batch).strip()
                if batch_str:
                    batch_numbers.append(batch_str)

        print(f"发现批次号: {batch_numbers}")

        # 提取参数信息（从第1行开始）
        parameters = []
        for row in range(1, df_data.shape[0]):
            param_id = df_data.iat[row, 1]
            description = df_data.iat[row, 2]

            if pd.notna(param_id) and str(param_id).strip() != 'ID':
                param_info = {
                    'id': str(param_id).strip(),
                    'description': str(description).strip() if pd.notna(description) else '',
                    'data': {}
                }

                for i, batch in enumerate(batch_numbers):
                    col_idx = 3 + i
                    if col_idx < df_data.shape[1]:
                        value = df_data.iat[row, col_idx]
                        param_info['data'][batch] = str(value).strip() if pd.notna(value) else ''

                parameters.append(param_info)

        print(f"提取到 {len(parameters)} 个参数")

        # 解析文档sheet
        document_data = {
            '通用文档': {},
            '单机文档': {}
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

            print(f"自动识别到文档分组: {list(group_mappings.keys())}")

            # 解析通用文档分组
            for group_name, group_info in group_mappings.items():
                if group_name == '单机文档' or group_info.get('is_batch'):
                    continue

                if group_info['columns']:
                    start_col = group_info['columns'][0]
                    print(f"{group_name}数据位于列 {start_col}-{start_col+2}")

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
                batch_columns = group_mappings['单机文档']['columns']
                batch_names = []

                for col in batch_columns:
                    if pd.notna(df_doc.iat[0, col]):
                        batch_names.append(str(df_doc.iat[0, col]).strip())
                    else:
                        batch_names.append(f"批次{col}")

                print(f"识别到 {len(batch_columns)} 个单机文档批次: {batch_names}")

                for batch_idx, batch_start_col in enumerate(batch_columns):
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

            print(f"文档数据: {len(document_data['通用文档'])}个通用文档, "
                  f"{sum(len(docs) for docs in document_data['单机文档'].values())}个单机文档")

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
    """根据占位符获取文档值，格式为: number《name》"""
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

    if doc_type == 'Doc':
        if batch in doc_data['单机文档'] and full_id in doc_data['单机文档'][batch]:
            item = doc_data['单机文档'][batch][full_id]
            return f"{item['number']}《{item['name']}》"
    else:
        if full_id in doc_data['通用文档']:
            item = doc_data['通用文档'][full_id]
            return f"{item['number']}《{item['name']}》"

    return None


EXCEL_TEMPLATE_EXTENSIONS = {'.xlsx', '.xls'}


def get_template_kind(template_filename):
    """根据模板文件名判断模板类型: excel 或 docx"""
    if template_filename:
        ext = os.path.splitext(str(template_filename))[1].lower()
        if ext in EXCEL_TEMPLATE_EXTENSIONS:
            return 'excel'
    return 'docx'


_PLACEHOLDER_RE = re.compile(r'\{\{([^{}]+)\}\}')


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
        print(f"  警告: 未找到数据或文档 {{{{{name}}}}}")
        return match.group(0)

    return _PLACEHOLDER_RE.sub(replace_one, value), param_count, doc_count


def _load_xls_workbook(template_file):
    """读取 .xls 模板，转换为 openpyxl Workbook（不保留 .xls 原生格式）。"""
    try:
        sheets = pd.read_excel(template_file, sheet_name=None, header=None)
    except Exception as e:
        raise ValueError(f"无法读取 .xls 模板，请安装 xlrd 或改用 .xlsx 模板: {e}")

    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for sheet_name, frame in sheets.items():
        ws = wb.create_sheet(title=str(sheet_name)[:31])
        for row_idx, row in enumerate(frame.itertuples(index=False, name=None), start=1):
            for col_idx, value in enumerate(row, start=1):
                if pd.notna(value):
                    ws.cell(row=row_idx, column=col_idx, value=value)
    return wb


def generate_excel_report(template_file, batch, param_data, doc_data, output_file,
                          template_filename=None):
    """
    使用 Excel 模板生成数据报告：遍历所有 sheet 的所有单元格并替换占位符。

    Returns:
        tuple: (success, param_replacements, doc_replacements, error)
    """
    try:
        from openpyxl import load_workbook

        context = {}
        for param in param_data:
            context[param['id']] = param['data'].get(batch, f'[数据缺失: {batch}]')

        ext = os.path.splitext(str(template_filename))[1].lower() if template_filename else ''
        if ext == '.xls':
            wb = _load_xls_workbook(template_file)
        else:
            wb = load_workbook(template_file, data_only=False)

        param_replacements = 0
        doc_replacements = 0
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    value = cell.value
                    if not isinstance(value, str) or '{{' not in value:
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

        print(f"  Excel 模板完成替换: {param_replacements}个参数, {doc_replacements}个文档")
        return True, param_replacements, doc_replacements, None
    except Exception as e:
        print(f"  错误: {e}")
        import traceback
        traceback.print_exc()
        return False, 0, 0, str(e)


def generate_consistency_report(param_data, batch_numbers, parameter_ids, output_file):
    """
    生成数据一致性对比 Word 报告。
    按选中的参数与批次，在表格中列出各批次数值，并给出是否一致的结论。

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
            consistent = len(normalized) <= 1
            if not consistent:
                inconsistent_count += 1
            row.cells[3 + len(selected_batches)].text = '一致' if consistent else '不一致'
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    for r in paragraph.runs:
                        r.font.size = Pt(9)

        summary = doc.add_paragraph()
        summary.add_run(
            f"共对比 {len(params)} 个参数、{len(selected_batches)} 个批次；"
            f"一致 {len(params) - inconsistent_count} 项，不一致 {inconsistent_count} 项。"
        )

        doc.save(output_file)
        if hasattr(output_file, 'seek'):
            output_file.seek(0)

        print(f"  一致性报告完成: {len(params)}个参数, {len(selected_batches)}个批次, "
              f"{inconsistent_count}项不一致")
        return True, len(params), inconsistent_count, None
    except Exception as e:
        print(f"  错误: {e}")
        import traceback
        traceback.print_exc()
        return False, 0, 0, str(e)


def _normalize_xml_for_jinja(xml_str):
    """
    清理 Word XML 中的跨 run 标签，使 Jinja2 能正确识别占位符。
    复用 docxtpl 的 patch_xml 逻辑。
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
    """
    from jinja2 import Template

    normalized = _normalize_xml_for_jinja(xml_str)
    template = Template(normalized)
    result = template.render(context)
    # 恢复可能被 Jinja2 转义的花括号
    result = result.replace("{_{", "{{").replace("}_}", "}}")
    result = result.replace("{_%", "{%").replace("%_}", "%}")
    return result


def generate_report(template_file, batch, param_data, doc_data, output_file,
                    template_filename=None):
    """
    生成数据报告。
    Excel 模板会遍历所有 sheet 的单元格替换占位符；
    Word 模板采用双层替换策略：
    1. 先用 docxtpl 处理主体内容（处理 doc() 函数等 Jinja2 高级特性）
    2. 再直接从 ZIP 层面确保文本框中的占位符被替换
       （防止旧版 python-docx 丢失 mc:AlternateContent 中的文本框内容）

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
    print(f"生成报告: {batch}")

    if hasattr(output_file, 'write'):
        print(f"输出到: 内存缓冲区")
    else:
        print(f"输出到: {output_file}")

    if get_template_kind(template_filename) == 'excel':
        return generate_excel_report(
            template_file, batch, param_data, doc_data, output_file, template_filename
        )

    try:
        from docxtpl import DocxTemplate

        # 获取文档类型前缀
        doc_types = get_doc_type_prefixes(doc_data)

        # 构建上下文
        context = {}

        for param in param_data:
            param_id = param['id']
            value = param['data'].get(batch, f'[数据缺失: {batch}]')
            context[param_id] = value

        def doc_lookup(ph):
            value = get_document_value(doc_data, ph, batch)
            if value:
                return value
            print(f"  警告: 未找到文档数据 {{{{{ph}}}}}")
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

        # 读取原始模板（用于对比文本框数量）
        if hasattr(template_file, 'read'):
            template_file.seek(0)
            template_bytes = template_file.read()
        else:
            with open(template_file, 'rb') as f:
                template_bytes = f.read()

        # 统计原始模板中的文本框数量
        with zipfile.ZipFile(io.BytesIO(template_bytes)) as src_zip:
            src_doc_xml = src_zip.read('word/document.xml').decode('utf-8')
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
            print(f"  检测到文本框问题: {fix_reason}，执行直接 XML 替换")

            # 从原始模板直接做 ZIP/XML 替换
            with zipfile.ZipFile(io.BytesIO(template_bytes)) as src_zip:
                doc_xml = src_zip.read('word/document.xml').decode('utf-8')
                doc_xml_replaced = _replace_in_xml_part(doc_xml, context)

                header_footer_parts = {}
                for name in src_zip.namelist():
                    if name.startswith('word/header') or name.startswith('word/footer'):
                        part_xml = src_zip.read(name).decode('utf-8')
                        header_footer_parts[name] = _replace_in_xml_part(part_xml, context)

            # 重建输出 ZIP，用替换后的 XML 覆盖
            final_buf = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(output_bytes)) as out_zip:
                with zipfile.ZipFile(final_buf, 'w', zipfile.ZIP_DEFLATED) as final_zip:
                    for item in out_zip.infolist():
                        data = out_zip.read(item.filename)
                        if item.filename == 'word/document.xml':
                            data = doc_xml_replaced.encode('utf-8')
                        elif item.filename in header_footer_parts:
                            data = header_footer_parts[item.filename].encode('utf-8')
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

        # 统计替换次数
        param_replacements = len(param_data)
        doc_replacements = len(doc_types) * 5

        print(f"  完成替换: {param_replacements}个参数, {doc_replacements}个文档")
        return True, param_replacements, doc_replacements, None

    except Exception as e:
        print(f"  错误: {e}")
        import traceback
        traceback.print_exc()
        return False, 0, 0, str(e)
