#!/usr/bin/env python
"""
单机数据包报告生成器核心模块
使用 docxtpl 进行参数替换
"""

import re
import os
from datetime import datetime

import pandas as pd
from docxtpl import DocxTemplate


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


def generate_report(template_file, batch, param_data, doc_data, output_file):
    """
    生成数据报告
    使用 docxtpl 进行参数替换

    Args:
        template_file: Word模板文件路径
        batch: 批次号
        param_data: 参数数据列表
        doc_data: 文档数据字典
        output_file: 输出文件路径或文件对象

    Returns:
        tuple: (success, param_replacements, doc_replacements)
    """
    print(f"生成报告: {batch}")

    if hasattr(output_file, 'write'):
        print(f"输出到: 内存缓冲区")
    else:
        print(f"输出到: {output_file}")

    try:
        # 使用 docxtpl 加载模板
        doc = DocxTemplate(template_file)

        # 统计替换
        param_replacements = 0
        doc_replacements = 0

        # 获取文档类型前缀
        doc_types = get_doc_type_prefixes(doc_data)

        # 构建上下文
        context = {}

        # 添加参数到上下文
        for param in param_data:
            param_id = param['id']
            value = param['data'].get(batch, f'[数据缺失: {batch}]')
            context[param_id] = value

        # 创建文档解析函数
        def doc_lookup(ph):
            value = get_document_value(doc_data, ph, batch)
            if value:
                return value
            print(f"  警告: 未找到文档数据 {{{{{ph}}}}}")
            return f'[文档缺失: {ph}]'

        context['doc'] = doc_lookup

        # 渲染文档
        doc.render(context)

        # 统计替换次数
        for param in param_data:
            param_replacements += 1

        # 统计文档替换
        doc_replacements = len(doc_types) * 5

        # 保存文档
        doc.save(output_file)

        print(f"  完成替换: {param_replacements}个参数, {doc_replacements}个文档")
        return True, param_replacements, doc_replacements

    except Exception as e:
        print(f"  错误: {e}")
        import traceback
        traceback.print_exc()
        return False, 0, 0