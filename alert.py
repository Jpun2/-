"""
RSI / MACD / 이동평균 복합 조건 알림 봇
- config.json 에 정의된 조건들을 확인하고, 전부(AND) 충족되면 텔레그램으로 알림을 보낸다.
- 조건은 고정값과 비교할 수도, 다른 지표와 비교할 수도 있다 (예: SMA20 > SMA50 골든크로스).
- 이미 알림을 보낸 조건은 (조건이 다시 풀릴 때까지) 중복으로 보내지 않는다. (state.json)
- GitHub Actions 에서 주기적으로 이 스크립트를 실행하는 걸 전제로 한다.
"""

import json
import os
import sys
import time
import requests

TWELVEDATA_BASE = "https://api.twelvedata.com"
CONFIG_PATH = "config.json"
STATE_PATH = "state.json"

TWELVEDATA_API_KEY = os.environ.get("TWELVEDATA_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

REQUEST_PAUSE_SEC = 0.4  # Twelve Data 무료 요금제 분당 요청 제한을 고려한 텀


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def td_get(endpoint, params):
    params = dict(params)
    params["apikey"] = TWELVEDATA_API_KEY
    resp = requests.get(f"{TWELVEDATA_BASE}/{endpoint}", params=params, timeout=20)
    data = resp.json()
    if isinstance(data, dict) and data.get("status") == "error":
        raise RuntimeError(f"{endpoint} 에러: {data.get('message')}")
    return data


def field_key(spec):
    """조건 하나를 데이터 캐시에서 찾기 위한 고유 키로 변환.
    rsi/sma/ema 는 기간이 다르면 다른 값이라 기간을 키에 포함시킨다."""
    t = spec["type"]
    if t in ("sma", "ema"):
        return f"{t}_{spec.get('period', 20)}"
    if t == "rsi":
        return f"rsi_{spec.get('period', 14)}"
    return t  # macd | macd_signal | macd_hist | price


def fetch_value(symbol, interval, spec):
    t = spec["type"]
    if t == "rsi":
        period = spec.get("period", 14)
        d = td_get("rsi", {"symbol": symbol, "interval": interval, "time_period": period, "outputsize": 1})
        return float(d["values"][0]["rsi"])
    if t in ("sma", "ema"):
        period = spec.get("period", 20)
        d = td_get(t, {"symbol": symbol, "interval": interval, "time_period": period, "outputsize": 1})
        return float(d["values"][0][t])
    if t == "price":
        d = td_get("price", {"symbol": symbol})
        return float(d["price"])
    if t in ("macd", "macd_signal", "macd_hist"):
        d = td_get("macd", {"symbol": symbol, "interval": interval, "outputsize": 1})
        row = d["values"][0]
        return float(row[t])
    raise ValueError(f"알 수 없는 지표 타입: {t}")


def gather_data(symbol, interval, conditions):
    """조건들(과 비교 대상들)이 필요로 하는 지표를 전부 모아서, 지표당 한 번씩만 조회한다."""
    needed = {}
    for c in conditions:
        needed[field_key(c)] = c
        if "compare_to" in c:
            needed[field_key(c["compare_to"])] = c["compare_to"]

    data = {}
    for key, spec in needed.items():
        data[key] = fetch_value(symbol, interval, spec)
        time.sleep(REQUEST_PAUSE_SEC)
    return data


OPS = {
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
    "==": lambda a, b: a == b,
}


def evaluate_conditions(conditions, data):
    """conditions 리스트의 모든 조건(AND)이 참인지 확인.
    각 조건은 고정 숫자(value) 또는 다른 지표(compare_to)와 비교할 수 있다."""
    for cond in conditions:
        left = data[field_key(cond)]
        if "compare_to" in cond:
            right = data[field_key(cond["compare_to"])]
        elif "value" in cond:
            right = cond["value"]
        else:
            raise ValueError(f"조건에 value 또는 compare_to 가 필요해요: {cond}")

        op = cond["op"]
        if op not in OPS:
            raise ValueError(f"알 수 없는 연산자: {op}")
        if not OPS[op](left, right):
            return False
    return True


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[경고] 텔레그램 설정이 없어서 메시지를 보내지 않음:", message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=20)
    if resp.status_code != 200:
        print("[경고] 텔레그램 전송 실패:", resp.text)


def format_data_summary(data):
    return " · ".join(f"{k}={v:.3f}" for k, v in sorted(data.items()))


def main():
    if not TWELVEDATA_API_KEY:
        print("TWELVEDATA_API_KEY 가 설정되지 않았어요.", file=sys.stderr)
        sys.exit(1)

    watchlist = load_json(CONFIG_PATH, [])
    state = load_json(STATE_PATH, {})

    if not watchlist:
        print("config.json 에 감시할 조건이 없어요.")
        return

    changed = False

    for watch in watchlist:
        watch_id = watch["id"]
        symbol = watch["symbol"]
        interval = watch.get("interval", "1day")
        conditions = watch["conditions"]
        message = watch.get("message", f"{symbol} 조건 충족")

        try:
            data = gather_data(symbol, interval, conditions)
            met = evaluate_conditions(conditions, data)
        except Exception as e:
            print(f"[{watch_id}] 처리 실패: {e}")
            continue

        was_alerted = state.get(watch_id, {}).get("alerted", False)

        print(f"[{watch_id}] {symbol} -> {format_data_summary(data)} | 조건충족={met} | 이전알림={was_alerted}")

        if met and not was_alerted:
            full_msg = f"🔔 {message}\n{symbol} | {format_data_summary(data)}"
            send_telegram(full_msg)
            state[watch_id] = {"alerted": True}
            changed = True
        elif not met and was_alerted:
            state[watch_id] = {"alerted": False}
            changed = True

    if changed:
        save_json(STATE_PATH, state)


if __name__ == "__main__":
    main()
