# Event Reminder Bot（事件提醒機器人）

輕量級 Telegram + 電郵提醒守護程序：監視一個 CSV 事件清單，於固定每日時段為到期事件發送整合提醒訊息（每頻道各一條）。僅使用 Python stdlib，無第三方套件。

## 用途

在定期付款與合約續期到期前，透過電郵（SMTP）與 Telegram bot 訊息提醒 Dennis 及各事件相關人士。

## 主要功能

### 1. 資料來源

事件存於 `data/event.csv`，欄位：

```
id,people,status,expires_on,early_remind_months,event,type,amount,currency,paid_on,note
```

- `id`：穩定編碼（R001、R002…），唯一，刪除後不可重用。
- `people`：自由文字，可列多個人。
- `status`：`active` 或 `inactive`；僅 `active` 行會收到提醒。
- `expires_on`：`YYYY-MM-DD`。
- `early_remind_months`：可選整數 N；若填寫，會在標準點之前加插 N、N−0.5、N−1… 個月前（日曆月計算，0.5 = 15 日）的早期提醒點。
- 其餘欄位會顯示在提醒訊息中。

每次運行後 CSV 會按 `expires_on` 升序重新排序，`inactive` 行置底；儲存格內容保持位元不變（含 UTF-8 中文完全保真）。每次寫入前先建立備份 `event.csv.bak`。

### 2. 提醒邏輯

- 每日於 `CHECK_HOUR`（預設 11 點，時區 `TZ_NAME` 預設 `Europe/London`）執行；DST 由 `zoneinfo` 處理。
- 標準提醒點：到期前 30、21、14、7、4、0 日。例：OVO 2026-12-19 到期，會於 11-19／11-28／12-05／12-12／12-15／12-19 觸發。
- 缺跑的日子（例如 NAS 關機）會合併為下一執行時的一條 catch-up 訊息，列出漏掉的點——絕不會變成「每日一則」的後遺訊息，也不會使用過期日期補發。
- 每個點在每個週期只發送一次；`data/state.json` 以 (id, expires_on, early_remind_months, 點) 為鍵記錄已發送狀態。
- 變更 `expires_on` 時，舊週期被靜默作廢並開始新週期。
- 週期中間新增行：若超過門檻（N 個月，或空白時為 30 日）則保持靜默至最近未來的點；若在門檻內則先發一條「即將到期」即時通知，之後剩餘未來點照常進行。已到過期的行永久靜默。
- `inactive`／已刪除的行會靜默從狀態中清除。
- 同一次運行到期的多行合併為：恰好一條 Telegram 訊息 + 一條電郵，各含兩行、按 id 順序排列。
- Telegram 與電郵發送器**互不干預**：一方失敗不會壓制另一方（該點仍被記錄，不會重發）。

### 3. 訊息格式（中文、固定格式）

```
事件提醒 — 2026-11-19
ID: R001 | Dennis | 事件: OVO Energy | 到期日: 2026-12-19 | 即將30日後到期
```

每行為固定管線格式：`ID: {id} | {people} | 事件: {event} | 到期日: {expires_on} | 即將{days}日後到期`；`note` 不為空時附 `| {note}`。到期當日為 `| 今日到期`。缺跑日合併為该行以 `| 補發` 結尾（漏掉的點合併入同一行，無回溯時間戳）。電郵主旨為 `事件提醒 — YYYY-MM-DD`，內文與 Telegram 文字完全相同。

## 使用／快速開始

```bash
cp .env.example data/.env      # 填入真實憑證
# 編輯 data/event.csv
python3 bot.py                 # 每日迴圈：下次於 CHECK_HOUR 執行
python3 bot.py --once          # 立即執行一次後離開（bot.run_pass 每次一個 pass）
DRY_RUN=1 python3 bot.py --once  # 只記錄日誌，不實際發送
touch data/run-now             # 要求運行之中的 container 執行一個 pass（日誌進入 container log）
touch data/run-now-dry         # 同上，惟為 dry-run pass
```

跑測試：

```bash
python3 -m unittest discover -s tests -v
```

全部 30 個驗收測試只用 Python stdlib、假日期與記憶體 fake senders——無網絡、無真實密碼。

## Docker

```bash
cp .env.example data/.env      # 填入真實憑證
docker compose up -d
```

容器掛載專案目錄，使 `data/event.csv` 與 `data/state.json` 於重啟後仍持久化於主機。

## 收件人設定

- `MAIL_TO` 選填，預設為 `SMTP_USER`。若要多收件人，用逗號分隔（一行 `MAIL_TO` 即可）：
  `MAIL_TO=dennis@example.com,family@example.com`
- 一條提醒訊息只會寄**一封**電郵（`To:` 欄含多個地址），不會逐個收件人各寄一封信。

## 支援平台

- Python 3.10+（原生運行）
- 任何 Docker 宿主機，例如 Synology DS920+（x86_64）

## 重要限制

- 每日每頻道只有一條整合訊息；發送到達時刻為 `CHECK_HOUR`（缺跑日子於下一執行時 catch-up，不按原時間補發）。
- `early_remind_months` 為整數月數；「0.5」固定為 15 日。
- `CHECK_HOUR` 與 `TZ_NAME` 於啟動時讀取；更改需編輯 `data/.env` 後重啟。

## 檔案

| 檔案 | 用途 |
|---|---|
| `bot.py` | 每日迴圈、發送器接線、訊息發出 |
| `event_reminder.py` | 核心邏輯：排程、CSV 儲存、狀態、發送器 |
| `data/event.csv` | 真實資料（用家維護；**不入 git** — 模板見 `data/event.example.csv`） |
| `data/state.json` | 每行已發送點狀態（首次執行後建立） |
| `data/.env` | 憑證／設定（永不 commit） |
| `.env.example` | 設定範本 |
| `tests/test_event_reminder.py` | 驗收測試（stdlib only） |
| `REQUIREMENTS.md` | 鎖定規格 |
| `PROJECT.md` / `DEPLOYMENT.md` / `TESTING.md` / `CHANGELOG.md` / `VERSION` | 專案文件 |
| `Dockerfile` / `docker-compose.yml` | 容器運行 |
| `FOLLOW_UP.md` | 交接筆記（保持更新） |
