#!/usr/bin/env python3
"""
宿舍洗烘衣機監控爬蟲
透過 iSeSA 隱藏 API (dispatch.ajax) 抓取機台狀態，產出 data.json 供前端讀取。
"""

import json
import random
import re
import sys
from datetime import datetime, timezone, timedelta

import requests

# ─── 店鋪設定 ──────────────────────────────────────────────
STORES = [
    {"code": "e2qsnj", "label": "店鋪 A"},
    {"code": "JjmsaA", "label": "店鋪 B"},
]

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
    """呼叫 iSeSA dispatch.ajax API，取得單一店鋪的機台資料。"""
    payload = {
        "code": code,
        "funcName": "F_CUSTOMER",
        "subFuncName": "SUB_QUERY_CODE",
        "ran": str(random.randint(0, 9999)),
    }
    resp = requests.post(API_URL, data=payload, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    text = resp.text
    # API 回傳前綴帶 "while(1);" 作為 JSON hijacking 防護
    if text.startswith("while(1);"):
        text = text[len("while(1);"):]

    return json.loads(text)


def classify_machine(alias: str) -> str:
    """根據機台名稱推斷類型：W=洗衣機, D=烘衣機。
    例如 '2W1' → washer, '3D1' → dryer, '2洗1' → washer, '2烘1' → dryer
    """
    alias_upper = alias.upper()
    if "W" in alias_upper or "洗" in alias:
        return "washer"
    elif "D" in alias_upper or "烘" in alias:
        return "dryer"
    return "unknown"


def normalize_machines(raw_list: list, group: int) -> list:
    """將 API 回傳的機台陣列正規化為統一格式。

    group 1: machineArray  (主要清單，用 lastRun 啟動時間)
    group 2: machineArray2 (次要清單，用 remainTime 剩餘時間)
    """
    machines = []
    for m in raw_list:
        status = "離線"
        if m.get("isOnline"):
            status = m.get("status", "未知")

        alias = m.get("gaiaMachineAlias", m.get("machineName", ""))
        machine_type = classify_machine(alias)

        remain = m.get("remainTime")   # 分鐘 (int or None)
        last_run = m.get("lastRun")    # UNIX timestamp (str or int or None)

        # lastRun 有時是字串
        if last_run is not None:
            try:
                last_run = int(last_run)
            except (ValueError, TypeError):
                last_run = None

        machines.append({
            "name": alias,
            "status": status,
            "remainTime": remain,
            "lastRun": last_run,
            "type": machine_type,  # washer / dryer / unknown
            "group": group,        # 1 or 2
        })
    return machines


def generate_recommendation(stores_data: list) -> dict:
    """根據所有店鋪的機台狀態，產生智慧推薦。"""
    best_store = None
    min_wait = float("inf")
    available_now = []

    for store in stores_data:
        idle_washers = [
            m for m in store["machines"]
            if m["type"] == "washer" and m["status"] in ("空機", "運轉結束")
        ]
        idle_dryers = [
            m for m in store["machines"]
            if m["type"] == "dryer" and m["status"] in ("空機", "運轉結束")
        ]

        if idle_washers or idle_dryers:
            available_now.append({
                "store": store["name"],
                "idleWashers": len(idle_washers),
                "idleDryers": len(idle_dryers),
            })

        # 計算該店最快可用時間（取運轉中機台剩餘最短者）
        running = [
            m for m in store["machines"]
            if m["status"] == "運轉中" and m.get("remainTime") is not None
        ]
        if running:
            store_min = min(m["remainTime"] for m in running)
            if store_min < min_wait:
                min_wait = store_min
                best_store = store["name"]

    if available_now:
        # 有空機 → 推薦空機最多的店
        top = max(available_now, key=lambda x: x["idleWashers"] + x["idleDryers"])
        return {
            "text": f"🟢 推薦前往「{top['store']}」，"
                    f"目前有 {top['idleWashers']} 台洗衣機、"
                    f"{top['idleDryers']} 台烘衣機可用！",
            "type": "available",
        }
    elif best_store:
        return {
            "text": f"🟡 目前皆無空機，最快約 {min_wait} 分鐘後，"
                    f"「{best_store}」會有機台可用。",
            "type": "wait",
        }
    else:
        return {
            "text": "🔴 目前無法取得即時資訊，請稍後再試。",
            "type": "unavailable",
        }


def main():
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz)
    stores_data = []
    errors = []

    for store_cfg in STORES:
        try:
            raw = fetch_store(store_cfg["code"])
            if not raw.get("success", False):
                errors.append(f"{store_cfg['label']}: API 回傳失敗")
                continue

            # API 回傳的資料在 jsonObj 底下
            json_obj = raw.get("jsonObj", raw)

            machines = []
            machines += normalize_machines(json_obj.get("machineArray", []), group=1)
            machines += normalize_machines(json_obj.get("machineArray2", []), group=2)

            stores_data.append({
                "name": store_cfg["label"],
                "code": store_cfg["code"],
                "laundryName": json_obj.get("laundryName", store_cfg["label"]),
                "address": json_obj.get("address", ""),
                "phone": json_obj.get("phone", ""),
                "machines": machines,
            })
        except Exception as exc:
            errors.append(f"{store_cfg['label']}: {exc}")

    recommendation = generate_recommendation(stores_data)

    output = {
        "lastUpdated": now.isoformat(),
        "stores": stores_data,
        "recommendation": recommendation,
        "errors": errors,
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"✅ data.json 已更新 ({now.strftime('%Y-%m-%d %H:%M:%S')})")
    if errors:
        print(f"⚠️  發生錯誤: {errors}", file=sys.stderr)


if __name__ == "__main__":
    main()
