# RSI / MACD 복합 조건 알림 봇

GitHub Actions가 30분마다(조정 가능) 자동으로 돌면서 `config.json`에 적어둔 조건을
전부 만족하는 순간 텔레그램으로 알림을 보내줍니다. PC나 폰이 꺼져있어도 동작합니다
(GitHub 서버에서 실행되니까요).

## 1. 이 폴더를 GitHub 저장소로 올리기

1. github.com 에서 새 저장소(Public) 생성 — 이름은 아무거나 (예: `rsi-alert-bot`)
   - Public으로 만들면 Actions 사용 시간이 완전 무료라 이걸 추천해요.
     (config.json에는 종목/조건만 있고 API 키는 안 들어가니 공개돼도 안전합니다)
2. 이 폴더 전체를 그 저장소에 업로드
   - GitHub 웹사이트에서 "Add file → Upload files"로 폴더 통째로 드래그해도 되고,
     git에 익숙하면 `git init && git add . && git commit -m "init" && git push` 로 올려도 됩니다.

## 2. 필요한 키 3개 발급받기

### Twelve Data API 키 (시세 데이터용, 무료)
1. https://twelvedata.com/pricing 에서 무료 가입
2. 대시보드에서 API 키 복사

### 텔레그램 봇 만들기 (알림 받을 용도, 무료)
1. 텔레그램 앱에서 `@BotFather` 검색 → 대화 시작
2. `/newbot` 입력 → 봇 이름/아이디 설정 → **Bot Token** 발급받음 (`123456:ABC-DEF...` 형태)
3. 새로 만든 내 봇을 검색해서 `/start` 한 번 눌러주기 (봇과 대화를 시작해야 메시지를 받을 수 있어요)
4. 내 **Chat ID** 알아내기: 텔레그램에서 `@userinfobot` 검색 → 대화 시작하면 내 Chat ID를 알려줍니다

## 3. GitHub 저장소에 키 등록하기 (Secrets)

저장소 페이지 → **Settings → Secrets and variables → Actions → New repository secret**
아래 3개를 각각 등록:

| Name | Value |
|---|---|
| `TWELVEDATA_API_KEY` | 위에서 발급받은 Twelve Data API 키 |
| `TELEGRAM_BOT_TOKEN` | BotFather가 준 Bot Token |
| `TELEGRAM_CHAT_ID` | userinfobot이 알려준 내 Chat ID |

**주의:** 이 키들은 절대 config.json이나 코드에 직접 적지 마세요. 반드시 위 Secrets 메뉴로만 등록하세요.

## 4. 조건 설정하기 — `config.json`

가장 배열 자체가 "감시 목록"이에요. 종목마다, 조건 조합마다 항목을 원하는 만큼 추가하면 됩니다.

### 기본 형태 (고정값과 비교)

```json
{
  "id": "고유한_이름_아무거나",
  "symbol": "CRM",
  "interval": "1day",
  "conditions": [
    { "type": "rsi", "period": 14, "op": "<=", "value": 30 },
    { "type": "macd_hist", "op": ">", "value": 0 }
  ],
  "message": "알림에 표시될 문구"
}
```

### 지표끼리 비교하기 (골든크로스, MACD 교차 등)

`value` 대신 `compare_to`를 쓰면 고정 숫자가 아니라 **다른 지표와** 비교해요.

```json
{ "type": "sma", "period": 20, "op": ">", "compare_to": { "type": "sma", "period": 50 } }
```
→ 20일 이동평균선이 50일 이동평균선보다 위로 올라오면 참 (골든크로스)

```json
{ "type": "macd", "op": ">", "compare_to": { "type": "macd_signal" } }
```
→ MACD선이 시그널선 위로 올라오면 참 (MACD 골든크로스)

### 사용 가능한 값

- `type`: `rsi`, `macd`, `macd_signal`, `macd_hist`, `sma`, `ema`, `price`
  - `rsi`, `sma`, `ema`는 `period`를 지정할 수 있어요 (안 쓰면 rsi=14, sma/ema=20이 기본값)
  - `macd` 계열은 기본 파라미터(12/26/9)를 사용해요
- `op`: `<=`, `>=`, `<`, `>`, `==`
- 조건마다 `value`(고정 숫자) 또는 `compare_to`(다른 지표) 둘 중 하나를 지정

### 여러 티커, 여러 조건 세트

- 같은 배열 안에 `symbol`이 다른 항목을 얼마든지 추가하면 여러 종목을 동시에 감시해요
- 같은 종목이라도 `id`를 다르게 해서 여러 조건 세트를 따로 만들 수 있어요 (예: "RSI만" 하나, "RSI+MACD 복합" 하나)
- `conditions` 배열 안의 조건은 **전부 AND**로 평가돼요 (하나라도 안 맞으면 알림 안 감). OR 조건이 필요하면 그냥 조건 세트를 두 개로 나눠서 등록하면 돼요.
- 한 번 알림이 가면, 조건이 다시 풀렸다가(거짓이 됐다가) 재충족될 때만 다시 알림이 갑니다 (스팸 방지)
- 지표를 조회할 때마다 API 요청이 나가니, 조건이 많아질수록 한 번 체크에 걸리는 시간과 요청 수가 늘어나요. 무료 요금제 한도를 넘지 않게 주의하세요.

## 5. 잘 되는지 테스트하기

1. GitHub 저장소 → **Actions** 탭 → "RSI/MACD Alert Check" 클릭
2. 오른쪽 "Run workflow" 버튼으로 수동 실행
3. 실행 로그에서 각 조건의 현재 RSI/MACD 값과 충족 여부 확인 가능
4. 조건을 이미 만족하는 종목/값으로 잠깐 바꿔서 텔레그램 알림이 실제로 오는지 테스트해보는 걸 추천

## 한계 / 참고사항

- 무료 Twelve Data 키는 분당 요청 수 제한이 있어요. 감시 종목이 많아지면 체크 주기를 늘리세요.
- `interval`이 `1day`인데 30분마다 체크하면 어차피 같은 값을 반복 조회하는 셈이라 낭비예요.
  일봉 기준이면 워크플로우의 cron을 하루 1~2회로 늘리는 걸 권장합니다.
- 이건 투자 참고용 신호일 뿐 투자 조언이 아니에요. 실제 매매 판단은 본인 책임입니다.
