# -*- coding: utf-8 -*-
"""UI 自动化测试用例导出为 Playwright（pytest + pytest-playwright）可直接执行的代码。

生成规则与 WHartTest_Actuator 执行器行为对齐：
- executor.py::_get_locator            → 定位器表达式（含 iframe / nth / 备用定位器说明）
- executor.py::_execute_step           → 页面操作 / 元素操作 / 断言映射
- executor.py::execute_page_step       → 每个页面步骤先 page.goto(页面地址)
- consumer.py::_build_page_step_config → input_value 提取优先级、case_data 覆盖、page_url 拼接
"""

import re
from datetime import datetime
from io import BytesIO
from json import dumps
from zipfile import ZIP_DEFLATED, ZipFile

README_CONTENT = """WHartTest UI 自动化用例 · Playwright 导出包
=============================================

运行准备:
    pip install pytest pytest-playwright
    playwright install chromium

步骤:
1. 将每个测试文件顶部的 BASE_URL 修改为目标环境地址（页面为相对路径时与其拼接）。
2. 执行测试:
    pytest test_case_xxx.py            # 无头模式
    pytest test_case_xxx.py --headed   # 有头模式，便于观察

说明:
- 与平台执行器行为一致：每个页面步骤执行前会先打开该步骤所属页面的地址。
- SQL / 自定义变量 / 条件判断 / Python 代码步骤无法在纯 Playwright 环境执行，导出时以注释保留。
- 上传文件步骤需要将路径替换为本地文件路径。
- 用例的前置/后置 SQL、参数化数据未包含在导出代码中。
"""

# 与 executor.py::_execute_step 的 element_operations / assert_operations 对齐
ELEMENT_OP_TEMPLATE = {
    'click': '{loc}.click()',
    'dblclick': '{loc}.dblclick()',
    'double_click': '{loc}.dblclick()',
    'right_click': '{loc}.click(button="right")',
    'fill': '{loc}.fill({value})',
    'type': '{loc}.type({value})',
    'clear': '{loc}.fill("")',
    'check': '{loc}.check()',
    'uncheck': '{loc}.uncheck()',
    'select': '{loc}.select_option({value})',
    'select_option': '{loc}.select_option({value})',
    'hover': '{loc}.hover()',
    'focus': '{loc}.focus()',
    'press': '{loc}.press({value})',
}

ASSERT_OP_TEMPLATE = {
    'visible': 'expect({loc}).to_be_visible()',
    'hidden': 'expect({loc}).to_be_hidden()',
    'enabled': 'expect({loc}).to_be_enabled()',
    'disabled': 'expect({loc}).to_be_disabled()',
    'checked': 'expect({loc}).to_be_checked()',
    'text': 'expect({loc}).to_have_text({value})',
    'value': 'expect({loc}).to_have_value({value})',
    'contain_text': 'expect({loc}).to_contain_text({value})',
    'url': 'expect(page).to_have_url({value})',
    'title': 'expect(page).to_have_title({value})',
    'count': 'expect({loc}).to_have_count({count})',
}

# consumer.py 中 case_data 简单值覆盖时的字段优先级
CASE_OVERRIDABLE_KEYS = ('text', 'value', 'timeout', 'url', 'key', 'expected')


def _py(value) -> str:
    """生成单行 Python 字符串字面量。"""
    return repr(str(value)) if value is not None else repr('')


def _safe_slug(name) -> str:
    s = re.sub(r'[^0-9A-Za-z_]+', '_', str(name or '')).strip('_').lower()
    return s[:40]


def _doc_safe(text: str) -> str:
    """用于 docstring 的单行安全文本。"""
    return ' '.join(str(text or '').replace('"""', '"').split())[:200]


def _apply_case_override(ope_value, case_data, detail_id):
    """复刻 consumer.py::_build_page_step_config 的 case_data 覆盖逻辑。"""
    if not case_data or detail_id not in case_data:
        return ope_value
    val = case_data[detail_id]
    if isinstance(val, dict):
        if isinstance(ope_value, dict):
            return {**ope_value, **val}
        return val
    if isinstance(ope_value, dict):
        for key in CASE_OVERRIDABLE_KEYS:
            if key in ope_value:
                return {**ope_value, key: val}
        if ope_value:
            first_key = next(iter(ope_value.keys()))
            return {**ope_value, first_key: val}
        return {'text': val}
    return val


def _extract_input_value(detail, case_data):
    """复刻 consumer.py 的 input_value 提取优先级：text→value→timeout→url→key→expected。"""
    ope_value = _apply_case_override(detail.get('ope_value'), case_data, str(detail.get('id', '')))
    if isinstance(ope_value, dict):
        input_value = (
            ope_value.get('text')
            or ope_value.get('value')
            or ope_value.get('timeout')
            or ope_value.get('url')
            or ope_value.get('key')
            or ope_value.get('expected')
            or ''
        )
        if not input_value and ope_value:
            input_value = next(iter(ope_value.values()), '')
        return str(input_value) if input_value else ''
    return str(ope_value) if ope_value else ''


def _parse_wait_timeout(value) -> int:
    """复刻 executor.py 中 wait 操作的超时解析（秒→毫秒，>60 兼容旧毫秒数据）。"""
    if not value:
        return 1000
    try:
        val = float(value)
    except (TypeError, ValueError):
        return 1000
    if val > 60:
        return int(val)
    return int(val * 1000)


def _locator_expr(detail) -> str:
    """生成与 executor.py::_get_locator 等价的定位器表达式（含 iframe 链与 nth 下标）。"""
    container = 'page'
    if detail.get('is_iframe') and detail.get('iframe_locator'):
        expr = str(detail['iframe_locator'])
        if '>>>' in expr:
            parts = [p.strip() for p in expr.split('>>>') if p.strip()]
        elif '>>' in expr:
            parts = [p.strip() for p in expr.split('>>') if p.strip()]
        else:
            parts = [expr.strip()]
        for part in parts:
            container += f'.frame_locator({_py(part)})'

    l_type = str(detail.get('locator_type') or 'xpath').lower()
    l_value = str(detail.get('locator_value') or '')

    get_by = {
        'text': 'get_by_text',
        'role': 'get_by_role',
        'placeholder': 'get_by_placeholder',
        'label': 'get_by_label',
        'testid': 'get_by_test_id',
    }.get(l_type)
    if get_by:
        loc = f'{container}.{get_by}({_py(l_value)})'
    else:
        if l_type == 'xpath':
            arg = 'xpath=' + l_value
        elif l_type == 'id':
            arg = '#' + l_value
        elif l_type == 'name':
            arg = "[name='" + l_value + "']"
        else:  # css 及未知类型，与执行器 fallback 一致
            arg = l_value
        loc = f'{container}.locator({_py(arg)})'

    index = detail.get('locator_index')
    if index is not None and index != '':
        try:
            loc += f'.nth({int(index)})'
        except (TypeError, ValueError):
            pass
    return loc


def _step_lines(detail, case_data):
    """将单个步骤详情转为代码行。

    Returns:
        tuple: (代码行列表, 是否包含可执行语句)
    """
    step_type = detail.get('step_type', 0)
    ope_key = str(detail.get('ope_key') or '').strip()
    operation = ope_key.lower()
    desc = detail.get('description') or detail.get('element_name') or ''
    title = f'步骤 {detail.get("step_sort", "")}: {desc}' if desc else f'步骤 {detail.get("step_sort", "")}'

    # SQL / 自定义变量 / 条件 / Python 代码步骤无法在纯 Playwright 环境执行，注释保留
    if step_type == 2:
        sql_cfg = detail.get('sql_execute') or {}
        sql = sql_cfg.get('sql') if isinstance(sql_cfg, dict) else str(sql_cfg)
        preview = ' '.join(str(sql or '').split())[:120]
        return [f'# [{title}] SQL 步骤导出时跳过: {preview}'], False
    if step_type == 3:
        custom = detail.get('custom')
        preview = dumps(custom, ensure_ascii=False) if custom else '-'
        return [f'# [{title}] 自定义变量步骤导出时跳过: {preview}'], False
    if step_type == 4:
        cond = detail.get('condition_value')
        preview = dumps(cond, ensure_ascii=False) if cond else '-'
        return [f'# [{title}] 条件判断步骤导出时跳过: {preview}'], False
    if step_type == 5:
        func = str(detail.get('func') or '')
        lines = [f'# [{title}] Python 代码步骤（已注释，请按需启用）:']
        for fn_line in (func.splitlines() or ['']):
            lines.append('# ' + fn_line)
        return lines, False

    lines = [f'# {title}']
    input_value = _extract_input_value(detail, case_data)

    # 步骤强制等待（与执行器一致：>60 视为毫秒误填自动转秒）
    wait_time = detail.get('wait_time') or 0
    if wait_time and wait_time > 0:
        if wait_time > 60:
            wait_time = wait_time / 1000
        lines.append(f'page.wait_for_timeout({int(wait_time * 1000)})')

    # 特殊操作：切换页签
    if operation == 'switch_tab':
        target = str(input_value or '').strip()
        if target and re.fullmatch(r'-?\d+', target):
            idx = int(target)
            lines.append(f'# 切换到页签索引 {idx}')
            lines.append(f'page = page.context.pages[{idx}]')
        elif target:
            lines.append(f'# 切换到匹配 {target!r} 的页签')
            lines.append(
                f'page = next((p for p in page.context.pages if {target!r} in p.url or {target!r} in p.title()), page)'
            )
        else:
            lines.append('# [switch_tab] 参数为空，跳过')
            return lines, False
        return lines, True

    if operation == 'screenshot':
        path = input_value or f'step_{detail.get("id", "x")}.png'
        lines.append(f'page.screenshot(path={path!r})')
        return lines, True

    # 页面操作（不需要定位器）
    if operation == 'goto':
        if input_value:
            lines.append(f'page.goto({_py(input_value)})')
            return lines, True
        lines.append('# [goto] 缺少 URL 参数，跳过')
        return lines, False
    if operation in ('reload', 'go_back', 'go_forward'):
        lines.append(f'page.{operation}()')
        return lines, True
    if operation == 'wait':
        lines.append(f'page.wait_for_timeout({_parse_wait_timeout(input_value)})')
        return lines, True
    if operation == 'wait_load':
        lines.append('page.wait_for_load_state("load")')
        return lines, True
    if operation == 'wait_network':
        lines.append('page.wait_for_load_state("networkidle")')
        return lines, True

    # 元素操作（需要定位器）
    l_value = str(detail.get('locator_value') or '').strip()
    if not l_value:
        lines.append('# 元素定位器为空，跳过该步骤')
        return lines, False

    loc = _locator_expr(detail)

    # 备用定位器仅作注释提示（导出代码只使用主定位器）
    backups = []
    for i in (2, 3):
        bv = detail.get(f'locator_value_{i}')
        if bv:
            backups.append(f'{detail.get(f"locator_type_{i}")}={bv}')
    if backups:
        lines.append('# 备用定位器（导出未使用）: ' + ', '.join(backups))

    if operation == 'upload':
        lines.append('# TODO: 上传文件步骤，请将下方路径替换为本地文件路径')
        lines.append(f'{loc}.set_input_files(r"<TODO_FILE_PATH>")')
        return lines, True

    template = ELEMENT_OP_TEMPLATE.get(operation)
    if template:
        lines.append(template.format(loc=loc, value=_py(input_value)))
        return lines, True

    if operation.startswith('assert_'):
        assert_type = operation[len('assert_'):]
        template = ASSERT_OP_TEMPLATE.get(assert_type)
        if template:
            if assert_type == 'count':
                try:
                    count = int(float(input_value)) if input_value else 0
                except (TypeError, ValueError):
                    count = 0
                lines.append(template.format(loc=loc, count=count))
            else:
                lines.append(template.format(loc=loc, value=_py(input_value)))
            return lines, True
        lines.append(f'# 未知断言类型: {ope_key}，跳过')
        return lines, False

    lines.append(f'# 未知操作类型: {ope_key}，跳过')
    return lines, False


def _case_file_stem(case) -> str:
    slug = _safe_slug(case.get('name'))
    return f'test_case_{case.get("id", 0)}' + (f'_{slug}' if slug else '')


def generate_case_code(case) -> str:
    """将 UiTestCaseExecuteSerializer 序列化后的单条用例数据转为 pytest 测试代码。"""
    case_id = case.get('id', 0)
    name = str(case.get('name') or f'case_{case_id}')
    module_name = str(case.get('module_name') or '-')
    level = str(case.get('level') or '-')
    description = _doc_safe(case.get('description'))

    lines = [
        '# -*- coding: utf-8 -*-',
        '"""',
        _doc_safe(name),
        '',
        f'来源: WHartTest UI自动化用例 #{case_id}',
        f'模块: {module_name}    等级: {level}',
    ]
    if description:
        lines.append(f'描述: {description}')
    lines += [
        '',
        '运行准备:',
        '    pip install pytest pytest-playwright',
        '    playwright install chromium',
        '',
        '注意:',
        '- 请将 BASE_URL 替换为目标环境地址',
        '- SQL / 自定义变量 / 条件 / Python 代码步骤以注释保留，不会执行',
        '- 上传文件步骤需替换为本地文件路径',
        '"""',
        'from playwright.sync_api import Page, expect',
        '',
        'BASE_URL = "http://localhost:8000"  # TODO: 替换为目标环境地址',
        '',
        '',
        'def _join_url(base_url: str, page_url: str) -> str:',
        '    """与执行器一致的页面地址拼接规则（相对路径与 BASE_URL 拼接）。"""',
        '    if not page_url:',
        '        return base_url',
        '    if page_url.startswith(("http://", "https://")):',
        '        return page_url',
        '    if page_url.startswith("/"):',
        '        return base_url.rstrip("/") + page_url',
        '    return base_url.rstrip("/") + "/" + page_url.lstrip("/")',
        '',
        '',
        f'def {_case_file_stem(case)}(page: Page) -> None:',
        f'    """{_doc_safe(name)}"""',
    ]

    has_code = False
    case_steps = sorted(
        case.get('case_step_details') or [],
        key=lambda s: s.get('case_sort') or 0,
    )
    for case_step in case_steps:
        page_step = case_step.get('page_step') or {}
        ps_name = str(page_step.get('name') or f'page_step_{page_step.get("id", "?")}')
        page_name = str(page_step.get('page_name') or '-')
        lines.append(f'    # ── 页面步骤 {case_step.get("case_sort", "")}: {ps_name}（页面: {page_name}）')

        page_url = str(page_step.get('page_url') or '')
        if page_url:
            lines.append(f'    page.goto(_join_url(BASE_URL, {_py(page_url)}))')
            lines.append('    page.wait_for_load_state("domcontentloaded")')
            has_code = True

        details = sorted(
            page_step.get('step_details') or [],
            key=lambda d: d.get('step_sort') or 0,
        )
        for detail in details:
            step_lines, executable = _step_lines(detail, case_step.get('case_data'))
            for line in step_lines:
                lines.append('    ' + line)
            if executable:
                has_code = True
        if not details:
            lines.append('    # 该页面步骤没有操作明细')

    if not case_steps:
        lines.append('    # 该用例没有引用页面步骤')
    if not has_code:
        lines.append('    pass')

    lines.append('')
    return '\n'.join(lines)


def build_export_bundle(cases_data):
    """将序列化后的用例数据打包为下载内容。

    Returns:
        tuple: (文件名, 内容 bytes, content_type)
            - 单个用例: 返回 .py 文件
            - 多个用例: 返回 zip 包（含 README.txt）
    """
    if len(cases_data) == 1:
        case = cases_data[0]
        code = generate_case_code(case)
        return _case_file_stem(case) + '.py', code.encode('utf-8'), 'text/x-python; charset=utf-8'

    buf = BytesIO()
    with ZipFile(buf, 'w', ZIP_DEFLATED) as zf:
        used_names = set()
        for case in cases_data:
            fname = _case_file_stem(case) + '.py'
            n = 2
            while fname in used_names:
                fname = f'{_case_file_stem(case)}_{n}.py'
                n += 1
            used_names.add(fname)
            zf.writestr(fname, generate_case_code(case))
        zf.writestr('README.txt', README_CONTENT)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    return f'ui_playwright_cases_{ts}.zip', buf.getvalue(), 'application/zip'
