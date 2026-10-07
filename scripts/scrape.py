#!/usr/bin/env python3
"""
宿舍洗烘衣機監控爬蟲
透過 iSeSA 隱藏 API 抓取機台狀態，並以「樓層」為單位統整資料。
"""

import json
import random
import re
import sys
import time
from datetime import datetime, timezone, timedelta

import requests

# 男二舍的兩個系統代碼
CODES = ["e2qsnj", "JjmsaA"]
API_URL = "http://monitor.isesa.com.tw/monitor/dispatch.ajax"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "http://monitor.isesa.com.tw/monitor/",
    "Origin": "http://monitor.isesa.com.tw",
}


def fetch_store(code: str) -> dict:
    """呼叫 API 取得資料"""
    payload = {
        "code": code,
        "funcName": "F_CUSTOMER",
        "subFuncName": "SUB_QUERY_CODE",
        "ran": str(random.randint(0, 9999)),
    }
    resp = requests.post(API_URL, data=payload, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    text = resp.text
    if text.startswith("while(1);"):
        text = text[len("while(1);"):]

    return json.loads(text)


def extract_floor(alias: str) -> int:
    """從機台名稱提取樓層 (例如 10W1 -> 10, 2洗1 -> 2)"""
    match = re.search(r'^(\d+)', alias)
    if match:
        return int(match.group(1))
    return 0


def classify_machine(alias: str) -> str:
    """判斷洗衣機或烘衣機"""
    alias_upper = alias.upper()
    if "W" in alias_upper or "洗" in alias:
        return "washer"
    elif "D" in alias_upper or "烘" in alias:
        return "dryer"
    return "unknown"


def process_machines(raw_list: list, current_timestamp: float) -> list:
    """正規化機台資料，計算 expectedEndTime (毫秒)"""
    machines = []
    for m in raw_list:
        status = "離線"
        if m.get("isOnline"):
            status = m.get("status", "未知")

        alias = m.get("gaiaMachineAlias", m.get("machineName", ""))
        machine_type = classify_machine(alias)
        floor = extract_floor(alias)

        remain = m.get("remainTime")
        last_run = m.get("lastRun")

        if last_run is not None:
            try:
                last_run = int(last_run)
            except (ValueError, TypeError):
                last_run = None

        expected_end_time = None
        if status == "運轉中":
            if machine_type == "washer" and last_run:
                # 洗衣機預設 50 分鐘
                expected_end_time = (last_run + 50 * 60) * 1000
            elif machine_type == "dryer" and remain is not None:
                # 烘衣機使用 API 給的剩餘時間計算絕對結束時間
                expected_end_time = current_timestamp * 1000 + remain * 60 * 1000

        machines.append({
            "name": alias,
            "floor": floor,
            "type": machine_type,
            "status": status,
            "expectedEndTime": expected_end_time,
        })
    return machines


def main():
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz)
    current_timestamp = time.time()
    
    all_machines = []
    errors = []

    for code in CODES:
        try:
            raw = fetch_store(code)
            if not raw.get("success", False):
                errors.append(f"代碼 {code}: API 回傳失敗")
                continue

            json_obj = raw.get("jsonObj", raw)
            all_machines += process_machines(json_obj.get("machineArray", []), current_timestamp)
            all_machines += process_machines(json_obj.get("machineArray2", []), current_timestamp)
        except Exception as exc:
            errors.append(f"代碼 {code}: {exc}")

    # 按照樓層分組
    floors_dict = {}
    for m in all_machines:
        f = m["floor"]
        if f not in floors_dict:
            floors_dict[f] = []
        floors_dict[f].append(m)

    floors_list = []
    for f in sorted(floors_dict.keys()):
        floors_list.append({
            "floor": f,
            "machines": floors_dict[f]
        })

    output = {
        "lastUpdated": now.isoformat(),
        "dormName": "男二舍",
        "floors": floors_list,
        "errors": errors,
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"✅ data.json 已更新 ({now.strftime('%Y-%m-%d %H:%M:%S')})")
    if errors:
        print(f"⚠️  發生錯誤: {errors}", file=sys.stderr)


if __name__ == "__main__":
    main()
