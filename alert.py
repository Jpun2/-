"""
RSI / MACD / 이동평균 복합 조건 알림 봇
- config.json 에 정의된 조건들을 확인하고, 전부(AND) 충족되면 텔레그램으로 알림을 보낸다.
- 조건은 고정값과 비교할 수도, 다른 지표와 비교할 수도 있다 (예: SMA20 > SMA50 골든크로스).
- "near" 연산자로 "가격이 이동평균선 근처(터치)"도 표현할 수 있다.
- 이미 알림을 보낸 조건은 (조건이 다시 풀릴 때까지) 중복으로 보내지 않는다. (state.json)
- 같은 지표(같은 심볼/주기)는 워크플로우 한 번 실행 중에 한 번만 조회해서 API 호출을 아낀다.
- Twelve Data 무료 요금제(분당 8회) 한도를 넘지 않도록 자체 속도 제한을 건다.
- GitHub Actions 에서 주기적으로 이 스크립트를 실행하는 걸 전제로 한다.
"""

import json
import os
import sys
import time
import collections
import requests

TWELVEDATA_BASE = "https://api.twelvedata.com"
CONFIG_PATH = "config.json"
STATE_PATH = "state.json"

TWELVEDATA_API_KEY = os.environ.get("TWELVEDATA_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Twelve Data 무료 요금제: 분당 8호출. 안전마진 두고 분당 6호출로 제한.
RATE_LIMIT_PER_MIN = 6
_call_times = collections.deque()


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def rate_limit_wait():
    """분당 호출 수를 RATE_LIMIT_PER_MIN 이하로 유지."""
    now = time.time()
    while _call_times and now - _call_times[0] > 60:
        _call_times.popleft()
    if len(_call_times) >= RATE_LIMIT_PER_MIN:
        sleep_sec = 60 - (now - _call_times[0]) + 1
        if sleep_sec > 0:
            print(f"  (요청 제한 대기 {sleep_sec:.0f}초...)")
            time.sleep(sleep_sec)
    _call_times.append(time.time())


def td_get(endpoint, params, retries=3):
    rate_limit_wait()
    params = dict(params)
    params["apikey"] = TWELVEDATA_API_KEY
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(f"{TWELVEDATA_BASE}/{endpoint}", params=params, timeout=20)
            data = resp.json()
            if isinstance(data, dict) and data.get("status") == "error":
                raise RuntimeError(f"{endpoint} 에러: {data.get('message')}")
            return data
        except (requests.RequestException, ValueError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
    raise RuntimeError(f"{endpoint} 요청 실패 (네트워크 문제로 추정): {last_err}")


def field_key(spec):
    """조건 하나를 데이터에서 찾기 위한 고유 키. 기간이 다르면 다른 값이라 키에 포함."""
    t = spec["type"]
    if t in ("sma", "ema"):
        return f"{t}_{spec.get('period', 20)}"
    if t == "rsi":
        return f"rsi_{spec.get('period', 14)}"
    return t  # macd | macd_signal | macd_hist | price


def fetch_value(symbol, interval, spec, raw_cache):
    """raw_cache: (symbol, interval, endpoint) -> API 응답. 같은 심볼/주기/엔드포인트는
    워크플로우 한 번 실행 중 한 번만 조회한다 (예: macd, macd_signal, macd_hist는
    전부 macd 엔드포인트 응답 하나를 공유)."""
    t = spec["type"]

    if t == "rsi":
        period = spec.get("period", 14)
        cache_key = (symbol, interval, "rsi", period)
        if cache_key not in raw_cache:
            raw_cache[cache_key] = td_get("rsi", {"symbol": symbol, "interval": interval, "time_period": period, "outputsize": 1})
        return float(raw_cache[cache_key]["values"][0]["rsi"])

    if t in ("sma", "ema"):
        period = spec.get("period", 20)
        cache_key = (symbol, interval, t, period)
        if cache_key not in raw_cache:
            raw_cache[cache_key] = td_get(t, {"symbol": symbol, "interval": interval, "time_period": period, "outputsize": 1})
        return float(raw_cache[cache_key]["values"][0][t])

    if t == "price":
        cache_key = (symbol, None, "price", None)
        if cache_key not in raw_cache:
            raw_cache[cache_key] = td_get("price", {"symbol": symbol})
        return float(raw_cache[cache_key]["price"])

    if t in ("macd", "macd_signal", "macd_hist"):
        cache_key = (symbol, interval, "macd", None)
        if cache_key not in raw_cache:
            raw_cache[cache_key] = td_get("macd", {"symbol": symbol, "interval": interval, "outputsize": 1})
        row = raw_cache[cache_key]["values"][0]
        return float(row[t])

    raise ValueError(f"알 수 없는 지표 타입: {t}")


def gather_data(symbol, interval, conditions, raw_cache):
    needed = {}
    for c in conditions:
        needed[field_key(c)] = c
        if "compare_to" in c:
            needed[field_key(c["compare_to"])] = c["compare_to"]

    data = {}
    for key, spec in needed.items():
        data[key] = fetch_value(symbol, interval, spec, raw_cache)
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
    각 조건은 고정 숫자(value), 다른 지표(compare_to), 또는 다른 지표 근처인지(near)와 비교할 수 있다."""
    for cond in conditions:
        left = data[field_key(cond)]
        op = cond["op"]

        if op == "near":
            # 예: 가격이 200일 이동평균선 +-tolerance_pct% 안에 들어오면 참 (터치로 간주)
            right = data[field_key(cond["compare_to"])]
            tolerance_pct = cond.get("tolerance_pct", 1.0)
            if right == 0:
                return False
            diff_pct = abs(left - right) / abs(right) * 100
            if diff_pct > tolerance_pct:
                return False
            continue

        if "compare_to" in cond:
            right = data[field_key(cond["compare_to"])]
        elif "value" in cond:
            right = cond["value"]
        else:
            raise ValueError(f"조건에 value 또는 compare_to 가 필요해요: {cond}")

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
    raw_cache = {}  # 이번 실행 전체에서 공유하는 API 응답 캐시

    for watch in watchlist:
        watch_id = watch["id"]
        symbol = watch["symbol"]
        interval = watch.get("interval", "1day")
        conditions = watch["conditions"]
        message = watch.get("message", f"{symbol} 조건 충족")

        try:
            data = gather_data(symbol, interval, conditions, raw_cache)
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
