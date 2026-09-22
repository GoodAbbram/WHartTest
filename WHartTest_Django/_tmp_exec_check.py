# -*- coding: utf-8 -*-
"""临时脚本：查今天的 UI 执行记录与批量记录（只读），结束后删除。"""
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wharttest_django.settings")
django.setup()

from django.utils import timezone as tz
from ui_automation.models import UiExecutionRecord, UiBatchExecutionRecord

today = tz.now().replace(hour=0, minute=0, second=0, microsecond=0)
qs = UiExecutionRecord.objects.filter(start_time__gte=today).order_by("-start_time")
print(f"今天单用例执行记录: {qs.count()} 条")
for r in qs[:20]:
    print(f"  id={r.id} case={r.test_case_id} status={r.status} trigger={r.trigger_type} start={r.start_time:%H:%M:%S}")

bqs = UiBatchExecutionRecord.objects.filter(start_time__gte=today).order_by("-start_time")
print(f"\n今天批量执行记录: {bqs.count()} 条")
for b in bqs[:20]:
    print(f"  id={b.id} name={b.name[:30]} total={b.total_cases} status={b.status} trigger={b.trigger_type} start={b.start_time:%H:%M:%S}")

# 全部记录里最近 10 条（含昨天以前），看泄漏从何时开始
print("\n最近 10 条执行记录（不限日期）:")
for r in UiExecutionRecord.objects.all().order_by("-start_time")[:10]:
    print(f"  id={r.id} case={r.test_case_id} status={r.status} start={r.start_time:%m-%d %H:%M:%S}")
