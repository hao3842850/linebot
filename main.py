# 天堂M 吃王小幫手

# === 1. 標準庫模組 (Standard Libraries) ===
import asyncio, json, os, threading, time, re,  traceback, uvicorn
from datetime import datetime, timedelta, timezone
from threading import Lock
from urllib.parse import urlparse

# === 2. 第三方套件 (Third-party Libraries) ===
import psycopg2, pytz, requests
from fastapi import FastAPI, Header, Request

# === 3. LINE Bot SDK 相關導入 ===
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import JoinEvent, MemberJoinedEvent, MessageEvent, TextMessage, TextSendMessage, FlexSendMessage, FlexContainer, BubbleContainer

# 基本設定
app, db_lock, active_auctions = FastAPI(), Lock(), {}
TZ, DB_FILE, DATABASE_URL = pytz.timezone("Asia/Taipei"), "database.json", os.getenv("DATABASE_URL")

# LINE Bot 相關設定
CHANNEL_SECRET, CHANNEL_TOKEN = os.getenv("LINE_CHANNEL_SECRET"), os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_ACCESS_TOKEN = CHANNEL_TOKEN  # 保留此變數以防您後續的程式碼有呼叫到它
line_bot_api, handler = LineBotApi(CHANNEL_TOKEN), WebhookHandler(CHANNEL_SECRET)

@app.on_event("startup")
def startup_event():
    init_db()
    init_fixed_boss_db()
    
# 工具函式
def is_peak_time(): return False  # 暫時關閉，永遠允許 Flex 訊息

def safe_init_dict(parent_dict, key):
    """安全初始化字典：防止資料型態錯誤，自動修復為空字典。"""
    if key not in parent_dict or not isinstance(parent_dict[key], dict):
        parent_dict[key] = {}
    return parent_dict[key]

def init_fixed_boss_db():
    """自動建立固定王專用的資料表"""
    print("🔧 系統啟動：檢查並建立 fixed_boss_records 資料表...")
    if not (conn := get_pg_conn()):
        return print("⚠️ 無法連線至資料庫，請檢查 DATABASE_URL 設定。")

    try:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS fixed_boss_records2 (id SERIAL PRIMARY KEY, group_id VARCHAR(255) NOT NULL, boss_name VARCHAR(255) NOT NULL, status VARCHAR(50) NOT NULL, record_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP);")
        conn.commit()
        print("✅ fixed_boss_records 資料表已確認存在 / 建立完成！")
    except Exception as e:
        print(f"❌ 自動建立資料表失敗: {e}")
        conn.rollback()
    finally:
        conn.close()

def check_subscription(group_id):
    """檢查訂閱：回傳 (是否允許, 到期時間, 狀態文字)"""
    if not (conn := get_pg_conn()): return True, None, "資料庫連線異常"
    
    try:
        cur, now = conn.cursor(), now_tw()
        cur.execute("SELECT status, expiry_date FROM subscriptions WHERE group_id = %s", (group_id,))
        
        # 1. 如果是新群組，自動給 7 天試用 (利用海象運算子同時賦值與判斷)
        if not (row := cur.fetchone()):
            cur.execute("INSERT INTO subscriptions (group_id, status, expiry_date) VALUES (%s, %s, %s)", 
                        (group_id, 'trial', (expiry := now + timedelta(days=7))))
            conn.commit()
            return True, expiry, "試用中"

        status, expiry = row
        # 2. 處理字串轉時間問題 (將 split 串聯，並使用單行 try-except)
        if isinstance(expiry, str):
            try: expiry = datetime.strptime(expiry.split('.')[0].split('+')[0], '%Y-%m-%d %H:%M:%S')
            except: return True, None, "時間格式解析失敗"

        # 3. 補上時區資訊 & 4. 判斷是否到期 (使用單行三元運算子)
        expiry = TZ.localize(expiry) if expiry.tzinfo is None else expiry
        return (False, expiry, "已到期") if now > expiry else (True, expiry, "授權有效")
        
    except Exception as e:
        print(f"訂閱檢查出錯: {e}")
        return True, None, "系統略過檢查"
    finally:
        cur.close()
        conn.close()

def build_subscription_flex(status, expiry_date):
    """建立訂閱到期的卡片回覆"""
    return FlexSendMessage(alt_text="訂閱到期通知", contents={
        "type": "bubble",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": "#222222", "contents": [{"type": "text", "text": "🔔 系統權限通知", "color": "#FFD700", "weight": "bold", "size": "lg"}]},
        "body": {"type": "box", "layout": "vertical", "spacing": "md", "contents": [
            {"type": "text", "text": f"目前狀態：{status}", "weight": "bold", "size": "md"},
            {"type": "text", "text": f"有效期限至：\n{expiry_date.strftime('%Y-%m-%d %H:%M')}", "size": "sm", "color": "#aaaaaa", "wrap": True},
            {"type": "separator", "margin": "lg"},
            {"type": "text", "text": "⚠️ 試用期已結束，功能已暫時鎖定。請聯絡管理員開通正式版以繼續使用。", "wrap": True, "size": "xs", "color": "#ff4444"}
        ]},
        "footer": {"type": "box", "layout": "vertical", "contents": [{"type": "button", "style": "primary", "color": "#FFD700", "action": {"type": "uri", "label": "聯絡開發者", "uri": "https://line.me/ti/p/wenhao0222"}}]}
    })

def safe_reply(event, text_msg, flex_msg=None):
    """安全回覆：自動判斷要傳送文字還是 Flex 訊息"""
    try: line_bot_api.reply_message(event.reply_token, TextSendMessage(text_msg) if is_peak_time() or not flex_msg else flex_msg)
    except Exception as e: print(f"Reply failed: {e}")

def get_source_id(event):
    """取得來源 ID，並將 B/C 群組狸貓換太子為 A 群組"""
    # 利用 getattr 動態取得 group_id, room_id 或 user_id
    s_id = getattr(event.source, f"{event.source.type}_id", getattr(event.source, "user_id", None))
    return "C5b59f9fe8a7c3b709742b8f765d8f95e" if s_id in ["Cfea8c07f23c410a1e328871f8573f5e5", "Cd75e1fb0598bb3508483751253707845"] else s_id

def now_tw(): return datetime.now(TZ)

def get_username(user_id):
    """取得玩家名稱"""
    try: return (p := get_roster_profile(user_id))["name"] if p else "未登記玩家"
    except: return "未知玩家"

def get_pg_conn():
    """建立資料庫連線並開啟自動提交"""
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        conn.autocommit = True 
        return conn
    except Exception as e: return print(f"❌ 資料庫連線失敗: {e}") # print 會回傳 None，符合原本邏輯
    
def save_boss_to_pg(group_id, boss_name, kill_time, respawn_time, user_id, note, source="manual"):
    """將單筆登記紀錄寫入資料庫"""
    if not (conn := get_pg_conn()): return
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO boss_time (group_id, boss_name, kill_time, respawn_time, user_id, note, source) VALUES (%s, %s, %s, %s, %s, %s, %s)", 
                        (group_id, boss_name, kill_time, respawn_time, user_id, note, source))
        conn.commit()
    except Exception as e: print(f"Error saving boss record: {e}")
    finally: conn.close()

def get_latest_boss_records(group_id):
    """取得各王最後一次登記紀錄 (依 id 降冪確保最後狀態)"""
    if not (conn := get_pg_conn()): return {}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT ON (boss_name) boss_name, kill_time, respawn_time, note, user_id, source FROM boss_time WHERE group_id = %s ORDER BY boss_name, id DESC", (group_id,))
            rows = cur.fetchall()
        
        # 建立簡短的 lambda 處理重複的時區轉換邏輯
        to_tz = lambda t: t.astimezone(TZ) if t.tzinfo else pytz.utc.localize(t).astimezone(TZ)
        
        # 透過 Tuple 解構與字典推導式，直接生成目標格式
        return {
            name: [{
                "date": (kt_tw := to_tz(kt)).strftime("%Y-%m-%d"), "kill": kt_tw.strftime("%H:%M:%S"),
                "respawn": to_tz(rt).isoformat(), "note": note or "", "user": user, "source": src
            }] for name, kt, rt, note, user, src in rows
        }
    except Exception as e:
        print(f"Error fetching boss records: {e}")
        return {}
    finally: conn.close()

def delete_boss_records_by_alias(group_id, input_text):
    """根據 alias_map 尋找對應的全名並徹底清除紀錄"""
    # 使用 next() 與生成器，一行完成遍歷與條件匹配
    target = next((k for k, v in alias_map.items() if input_text in v or input_text == k), None)
    if not target or not (conn := get_pg_conn()): return False, target
    
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM boss_time WHERE group_id = %s AND boss_name = %s", (group_id, target))
        conn.commit()
        return cur.rowcount > 0, target
    except Exception as e:
        print(f"SQL 刪除出錯: {e}")
        return False, target
    finally: conn.close()

def get_kpi_ranking(group_id):
    """取得區間內的 KPI 統計排行"""
    if not (conn := get_pg_conn()): return "資料庫連線失敗", []
    try:
        st, et = get_kpi_range(now_tw())
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, COUNT(*) FROM boss_time WHERE group_id = %s AND kill_time >= %s AND kill_time < %s AND source != 'boot' GROUP BY user_id ORDER BY COUNT(*) DESC", (group_id, st, et))
            # 將日期格式化與列表推導式合併在 return，直接產出最終結構
            return f"{st.strftime('%m/%d')} ~ {et.strftime('%m/%d')}", [(get_username(uid), count) for uid, count in cur.fetchall()]
    except Exception as e:
        print(f"KPI Error: {e}")
        return "統計出錯", []
    finally: conn.close()

def delete_all_boss_records(group_id):
    """確實執行 SQL 刪除"""
    if not (conn := get_pg_conn()): return
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM boss_time WHERE group_id = %s", (group_id,))
        conn.commit()  # <--- 這行沒寫資料永遠刪不掉
        print(f"PostgreSQL 刪除完成: {group_id}")
    except Exception as e: print(f"SQL 刪除出錯: {e}")
    finally: conn.close()

def get_all_records_for_kpi(group_id, start_time, end_time):
    """抓取區間內所有紀錄，並格式化為符合 calculate_kpi 要求的 dict 格式"""
    if not (conn := get_pg_conn()): return {}
    records = {}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT boss_name, kill_time, user_id, source FROM boss_time WHERE group_id = %s AND kill_time >= %s AND kill_time < %s", (group_id, start_time, end_time))
            for boss, kt, uid, src in cur.fetchall():
                # 利用 setdefault 直接省去 if boss not in records 的判斷
                records.setdefault(boss, []).append({"date": kt.strftime("%Y-%m-%d"), "kill": kt.strftime("%H:%M:%S"), "user": uid, "source": src})
        return records
    finally: conn.close()

def background_check():
    """背景定時檢查大王重生狀態並通知"""
    while True:
        try:
            if conn := get_pg_conn():
                try:
                    now = now_tw()
                    with conn.cursor() as cur:
                        cur.execute("SELECT group_id, boss_name, respawn_time FROM boss_time")
                        for gid, boss, rt in cur.fetchall():
                            rt = TZ.localize(rt) if rt.tzinfo is None else rt
                            # 【核心修改】合併時間判斷與大王名單判斷，瞬間消滅兩層縮排
                            if 270 <= (rt - now).total_seconds() < 330 and boss in MAJOR_BOSSES:
                                notify_boss_team(gid, boss)
                finally: conn.close()
        except Exception as e: print(f"背景檢查發生錯誤: {e}")
        time.sleep(60)

# 啟動背景執行緒 (單行化設定與啟動)
threading.Thread(target=background_check, daemon=True).start()

# 1. 定義需要 @標記 的大王清單 (名稱需與 cd_map 一致)
MAJOR_BOSSES = ["古代巨人", "不死鳥", "死亡騎士", "克特"]

def notify_boss_team(group_id, boss_name):
    """通知打王組：建構 LINE Mention Payload 或發送普通訊息"""
    if not (conn := get_pg_conn()): return
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM boss_team WHERE group_id = %s", (group_id,))
            rows = cur.fetchall()
            
        base_msg = f"【{boss_name}】即將在 5 分鐘後重生！"
        if not rows: # 沒人時發送普通訊息並提早結束 (Early Return)
            return line_bot_api.push_message(group_id, TextSendMessage(text=f"⏰ {base_msg}"))

        # 有成員則利用列表推導式建構標記名單
        prefix = "📢 打王組集合！ "
        mentionees = [{"index": len(prefix) + i, "length": 1, "userId": str(r[0])} for i, r in enumerate(rows[:50])]
        
        # 利用 requests 的 json 參數自動轉譯字典並省去 Content-Type 設定
        res = requests.post("https://api.line.me/v2/bot/message/push", 
                            headers={"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"},
                            json={"to": group_id, "messages": [{"type": "text", "text": f"{prefix}{' ' * len(mentionees)}\n{base_msg}", "mention": {"mentionees": mentionees}}]})
        
        if res.status_code != 200: print(f"LINE API 報錯: {res.text}")
            
    except Exception as e: print(f"通知過程發生錯誤: {e}")
    finally: conn.close()
   
def build_confirmation_flex(boss_name):
    """建立擊殺確認的 Flex Message"""
    return FlexSendMessage(alt_text=f"{boss_name} 擊殺確認", contents={
        "type": "bubble", "size": "kilo",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": "#2C3E50", "paddingAll": "15px", "contents": [
            {"type": "text", "text": "⚔️ 擊殺確認", "color": "#ffffff", "weight": "bold", "align": "center", "size": "sm", "opacity": "0.8"},
            {"type": "text", "text": boss_name, "color": "#FFD700", "weight": "bold", "align": "center", "size": "xl", "wrap": True, "margin": "sm"}
        ]},
        "body": {"type": "box", "layout": "vertical", "spacing": "lg", "paddingAll": "20px", "contents": [
            {"type": "text", "text": "王 5 分鐘後即將重生\n請盡速回報結果：", "size": "sm", "color": "#555555", "align": "center", "weight": "bold", "wrap": True},
            {"type": "box", "layout": "horizontal", "spacing": "md", "contents": [
                {"type": "button", "style": "primary", "color": "#4A90E2", "action": {"type": "message", "label": "我方擊殺", "text": f"紀錄 我方擊殺 {boss_name}"}},
                {"type": "button", "style": "primary", "color": "#FF5252", "action": {"type": "message", "label": "敵人吃", "text": f"紀錄 敵人吃 {boss_name}"}}
            ]}
        ]}
    })

def send_delayed_confirmation(group_id, boss_name):
    """5分鐘後執行的推播函式"""
    try: line_bot_api.push_message(group_id, build_confirmation_flex(boss_name))
    except Exception as e: print(f"延遲發送卡片失敗: {e}")

def auto_mark_missed(group_id, boss_name):
    """計時結束後檢查是否有回報，若無則自動記錄為漏掉"""
    if not (conn := get_pg_conn()): return
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM fixed_boss_records2 WHERE group_id = %s AND boss_name = %s AND record_time >= NOW() - INTERVAL '15 minutes'", (group_id, boss_name))
            if not cur.fetchone():
                cur.execute("INSERT INTO fixed_boss_records2 (group_id, boss_name, status) VALUES (%s, %s, '漏掉')", (group_id, boss_name))
                conn.commit()
                print(f"✅ {boss_name} 超時未回報，已自動寫入：漏掉")
    except Exception as e:
        print(f"❌ 自動標記漏掉失敗: {e}")
        conn.rollback()
    finally: conn.close()
        
def init_db():
    if not os.path.exists(DB_FILE):
        with open(DB_FILE, "w", encoding="utf-8") as f: json.dump({"boss": {}}, f, ensure_ascii=False, indent=2)

def load_db():
    with db_lock, open(DB_FILE, "r", encoding="utf-8") as f: return json.load(f)

def save_db(db):
    with db_lock, open(DB_FILE, "w", encoding="utf-8") as f: json.dump(db, f, ensure_ascii=False, indent=2)

init_db()

def build_all_boss_quick_flex():
    """建立全王快速登記面板"""
    bosses = sorted(cd_map.keys())
    rows = []
    
    # 利用列表推導式與陣列乘法，大幅簡化按鈕建構與補齊空格的邏輯
    for i in range(0, len(bosses), 4):
        cols = [{"type": "box", "layout": "vertical", "backgroundColor": "#4682B4", "cornerRadius": "md", "paddingAll": "8px", 
                 "contents": [{"type": "text", "text": name, "size": "xxs", "align": "center", "color": "#ffffff", "weight": "bold", "gravity": "center"}],
                 "action": {"type": "message", "label": name, "text": f"6666 {name}"}} for name in bosses[i:i+4]]
        
        cols += [{"type": "spacer", "flex": 1}] * (4 - len(cols))  # 🌟 直接用陣列相加與乘法補齊不足的空格
        rows.append({"type": "box", "layout": "horizontal", "spacing": "xs", "contents": cols})

    return FlexSendMessage(alt_text="快速登記選單", contents={
        "type": "bubble",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": "#2c3e50", "contents": [{"type": "text", "text": "快速登記 (6666)", "weight": "bold", "color": "#ffffff", "size": "sm", "align": "center"}]},
        "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": rows}
    })

def build_undo_flex(boss_name, k_time_str, r_time_str, note=None):
    """建立撤銷成功的 Flex Message"""
    # 備註欄位：單行三元運算子
    note_box = {"type": "box", "layout": "vertical", "margin": "lg", "spacing": "sm", "contents": [{"type": "box", "layout": "baseline", "spacing": "sm", "contents": [{"type": "text", "text": "備註", "color": "#aaaaaa", "size": "sm", "flex": 1}, {"type": "text", "text": str(note), "wrap": True, "color": "#666666", "size": "sm", "flex": 4}]}]} if note else {"type": "filler"}

    return {
        "type": "bubble",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": "#27ae60", "contents": [
            {"type": "text", "text": "撤銷成功", "weight": "bold", "color": "#FFFFFF", "size": "sm"},
            {"type": "text", "text": boss_name, "weight": "bold", "size": "xxl", "margin": "md", "color": "#FFFFFF"}
        ]},
        "body": {"type": "box", "layout": "vertical", "contents": [
            {"type": "text", "text": "📊 系統已回溯至上一筆紀錄", "size": "sm", "color": "#111111", "weight": "bold"},
            {"type": "separator", "margin": "md"},
            {"type": "box", "layout": "vertical", "margin": "lg", "spacing": "sm", "contents": [
                {"type": "box", "layout": "baseline", "spacing": "sm", "contents": [{"type": "text", "text": "擊殺", "color": "#aaaaaa", "size": "sm", "flex": 1}, {"type": "text", "text": k_time_str, "wrap": True, "color": "#666666", "size": "sm", "flex": 4}]},
                {"type": "box", "layout": "baseline", "spacing": "sm", "contents": [{"type": "text", "text": "重生", "color": "#aaaaaa", "size": "sm", "flex": 1}, {"type": "text", "text": r_time_str, "wrap": True, "color": "#666666", "size": "sm", "flex": 4}]}
            ]},
            note_box
        ]},
        "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
            {"type": "text", "text": "提示：輸入「打王」查看完整清單", "style": "italic", "size": "xs", "color": "#aaaaaa", "align": "center"}
        ]}
    }
# 這是暫時的名單，之後你可以隨時替換成你給我的真實名單
MAYBE_SKIP_BOSSES = ["小紅", "小綠", "守護螞蟻", "巨大蜈蚣", "伊弗利特", "大腳瑪幽", "巨大飛龍", "力卡溫", "卡司特王", "變形怪首領", "古代巨人", "不死鳥", "克特", "賽尼斯的分身", "貝里斯", "烏勒庫斯", "奈克偌斯"] 

def build_kill_list_flex(title, display_items):
    """建立打王清單 Flex Message"""
    rows, now = [], now_tw()

    for i, (dt, line_text) in enumerate(display_items):
        # 1. 拆解時間與王名字串
        time_str, *boss_info_list = line_text.split(" ", 1)
        boss_info = boss_info_list[0] if boss_info_list else ""
        pure_name = boss_info.split("（")[0].split(" <")[0].split(" #")[0].strip()

        # 2. 判斷重生狀態顏色 (單行三元運算子)
        diff = (dt - now).total_seconds()
        bg_color, status_text = ("#FF5252", "已重生") if diff < 0 else ("#FFB74D", "即將") if diff < 1800 else ("#66BB6A", "等待")

        # 3. 判斷是否需要輪空按鈕，並動態調整王名的 flex 寬度
        show_skip = pure_name in MAYBE_SKIP_BOSSES and "#過" not in boss_info
        
        # 4. 水平化宣告所有 UI 元件
        kill_btn = {"type": "box", "layout": "vertical", "flex": 2, "backgroundColor": "#4A90E2", "cornerRadius": "lg", "paddingAll": "6px", "contents": [{"type": "text", "text": "擊殺", "size": "xs", "color": "#ffffff", "align": "center", "weight": "bold"}], "action": {"type": "message", "label": "K", "text": f"6666 {pure_name}"}}
        time_box = {"type": "box", "layout": "vertical", "flex": 3, "backgroundColor": bg_color, "cornerRadius": "md", "paddingAll": "4px", "contents": [{"type": "text", "text": time_str, "size": "xxs", "color": "#ffffff", "weight": "bold", "align": "center"}, {"type": "text", "text": status_text, "size": "xxs", "color": "#ffffff", "align": "center", "opacity": "0.9", "margin": "2px"}]}
        name_box = {"type": "text", "text": boss_info, "size": "sm", "weight": "bold", "flex": 5 if show_skip else 7, "gravity": "center", "wrap": True}
        skip_btn = {"type": "box", "layout": "vertical", "flex": 2, "backgroundColor": "#9E9E9E", "cornerRadius": "lg", "paddingAll": "6px", "contents": [{"type": "text", "text": "輪空", "size": "xs", "color": "#ffffff", "align": "center", "weight": "bold"}], "action": {"type": "message", "label": "Skip", "text": f"{pure_name} 空"}}
        
        # 5. 組合列，並根據是否為第一列來加入分隔線
        row_contents = [kill_btn, time_box, name_box] + ([skip_btn] if show_skip else [])
        if i > 0: rows.append({"type": "separator", "margin": "lg", "color": "#ECECEC"})
        rows.append({"type": "box", "layout": "horizontal", "margin": "lg" if i > 0 else "md", "spacing": "sm", "alignItems": "center", "contents": row_contents})

    return FlexSendMessage(alt_text=title, contents={
        "type": "bubble", "size": "mega",
        "header": {"type": "box", "layout": "horizontal", "alignItems": "center", "backgroundColor": "#2C3E50", "paddingAll": "15px", "contents": [
            {"type": "text", "text": title, "color": "#ffffff", "weight": "bold", "size": "md", "flex": 1},
            {"type": "button", "action": {"type": "message", "label": "交班", "text": "交班"}, "style": "primary", "color": "#1DB100", "height": "sm", "flex": 0}
        ]},
        "body": {"type": "box", "layout": "vertical", "spacing": "none", "paddingAll": "20px", "contents": rows or [{"type": "text", "text": "目前尚無重生資料", "align": "center", "color": "#aaaaaa", "size": "sm", "margin": "xl"}]},
        "footer": {"type": "box", "layout": "vertical", "paddingAll": "10px", "contents": [{"type": "button", "action": {"type": "message", "label": "🔄 更新清單", "text": "打王"}, "style": "secondary", "height": "sm"}]},
        "styles": {"footer": {"separator": True}}
    })

def notify_boss_team_with_flex(group_id, boss_name):
    """通知打王組 (包含文字標記與 Flex 警告卡片)"""
    if not (conn := get_pg_conn()): return
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM boss_team WHERE group_id = %s", (group_id,))
            rows = cur.fetchall()

        # 1. 單行處理：如果有成員就生成標記名單與專屬文字，否則回傳一般文字與 None
        base, prefix = f"【{boss_name}】即將在 5 分鐘後重生！", "📢 打王組集合！ "
        mentionees = [{"index": len(prefix) + i, "length": 1, "userId": r[0]} for i, r in enumerate(rows[:50])]
        full_text, mention = (f"{prefix}{' ' * len(mentionees)}\n{base}", {"mentionees": mentionees}) if rows else (f"⏰ 提醒：{base}", None)

        # 2. 直接在 push_message 中組合 Text 與 Flex 陣列
        line_bot_api.push_message(group_id, [
            TextSendMessage(text=full_text, mention=mention),
            FlexSendMessage(alt_text=f"警報: {boss_name}", contents={
                "type": "bubble", "size": "sm",
                "header": {"type": "box", "layout": "vertical", "backgroundColor": "#E74C3C", "contents": [{"type": "text", "text": "⚔️ 大王警告", "color": "#ffffff", "weight": "bold", "size": "sm", "align": "center"}]},
                "body": {"type": "box", "layout": "vertical", "contents": [{"type": "text", "text": boss_name, "weight": "bold", "size": "xl", "align": "center", "margin": "md"}, {"type": "text", "text": "準備重生", "size": "sm", "color": "#aaaaaa", "align": "center"}]}
            })
        ])
    except Exception as e: print(f"通知出錯: {e}")
    finally: conn.close()

def undo_last_boss_record(group_id, input_text):
    """撤銷最近一筆登記，並回傳更新後的 Flex Message"""
    # 1. 尋找目標王名 (單行生成器)
    target = next((k for k, v in alias_map.items() if input_text in v or input_text == k), None)
    if not target: return False, TextSendMessage(text="❌ 找不到該王名，請確認輸入是否正確。")
    if not (conn := get_pg_conn()): return False, TextSendMessage(text="❌ 資料庫連線失敗")
    
    try:
        with conn.cursor() as cur:
            # 2. 刪除最後一筆紀錄
            cur.execute("DELETE FROM boss_time WHERE id = (SELECT id FROM boss_time WHERE group_id = %s AND boss_name = %s ORDER BY id DESC LIMIT 1) RETURNING id;", (group_id, target))
            if not cur.fetchone(): return False, TextSendMessage(text=f"❌ 找不到 {target} 的任何登記紀錄可供撤銷。")
            conn.commit()

            # 3. 查詢更新後的最新紀錄
            cur.execute("SELECT kill_time, respawn_time, note FROM boss_time WHERE group_id = %s AND boss_name = %s ORDER BY id DESC LIMIT 1", (group_id, target))
            if not (rec := cur.fetchone()): return True, TextSendMessage(text=f"✅ 已撤銷 {target} 的唯一紀錄，目前無登記資料。")
            
            # 4. 處理時區與卡片建構
            to_tz = lambda t: t.astimezone(TZ) if t.tzinfo else pytz.utc.localize(t).astimezone(TZ)
            flex = build_undo_flex(target, to_tz(rec[0]).strftime('%H:%M:%S'), to_tz(rec[1]).strftime('%H:%M:%S'), rec[2])
            return True, FlexSendMessage(alt_text=f"撤銷成功：{target}", contents=flex)
            
    except Exception as e:
        print(f"撤銷邏輯出錯: {e}")
        return False, TextSendMessage(text="⚠️ 系統處理出錯，請稍後再試。")
    finally: conn.close()

def build_register_boss_flex(boss, kill_time, respawn_time, registrar, note=None, is_skip=False, is_b_group=False):
    """建立登記成功的 Flex Message 卡片"""
    warning = None
    try:
        now = now_tw() # 沿用之前的工具函式
        # 1. 時間處理：利用 datetime.combine 直接將今天的日期與解析出的時間合併
        rt = datetime.strptime(kill_time.strip(), "%H:%M:%S").time()
        record_time = TZ.localize(datetime.combine(now.date(), rt))
        if record_time > now + timedelta(minutes=10): record_time -= timedelta(days=1)
        
        # 30 分鐘前 ~ 12 小時內的紀錄，產生警告區塊
        if 1800 < (now - record_time).total_seconds() < 43200:
            warning = {"type": "box", "layout": "vertical", "margin": "md", "backgroundColor": "#FFEEEE", "cornerRadius": "md", "paddingAll": "sm", "contents": [{"type": "text", "text": "⚠️ 注意：此為 30 分鐘前的紀錄！", "color": "#FF0000", "size": "xs", "weight": "bold", "align": "center"}]}
    except Exception as e: print(f"DEBUG - 時間解析失敗: {e}")

    # 2. 樣式與變數設定 (單行三元運算子)
    map_text = "、".join(BOSS_MAP.get(boss, [])) or "未知"
    g_tag, bg_color = ("【特殊】", "#F3E5F5") if is_b_group else ("", "#FFFFFF")
    b_color = ("#A020F086" if is_skip else "#00BCD4") if is_b_group else ("#A020F0" if is_skip else "#FF6D18")
    prefix, t_label = (f"{g_tag}⭕ 輪空登記 ", "🕒 輪空：") if is_skip else (f"{g_tag}🔥 已登記 ", "🕒 死亡：")

    # 3. 基礎資訊列 (列表推導式)
    rows_ui = [{"type": "box", "layout": "baseline", "contents": [{"type": "text", "text": k, "size": "sm", "color": "#888888", "flex": 2}, {"type": "text", "text": v, "wrap": True, "flex": 6}]} for k, v in [("🗺️ 地圖：", map_text), (t_label, kill_time), ("✨ 重生：", respawn_time)]]

    # 4. 動態組合所有的 UI 內容 (陣列相加)
    contents = [
        {"type": "text", "text": prefix, "weight": "bold", "size": "lg", "contents": [{"type": "span", "text": prefix}, {"type": "span", "text": boss, "color": b_color, "weight": "bold"}]}
    ] + ([warning] if warning else []) + [{"type": "separator", "margin": "md"}] + rows_ui + (
        [{"type": "box", "layout": "baseline", "contents": [{"type": "text", "text": "📌 備註：", "size": "sm", "color": "#888888", "flex": 2}, {"type": "text", "text": note, "wrap": True, "flex": 6}]}] if note else []
    ) + [{"type": "separator", "margin": "lg"}, {"type": "text", "text": f"👤 登記者：{registrar}", "size": "xs", "color": "#999999", "wrap": True}]

    # 5. 回傳 Flex 容器
    return FlexSendMessage(alt_text=f"{prefix}{boss}", contents={
        "type": "bubble", "styles": {"body": {"backgroundColor": bg_color}},
        "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": contents}
    })

def build_register_boss_text(boss, kill_time, respawn_time, registrar, note):
    """建立登記成功的純文字回覆"""
    return f"已登記 {boss}\n地圖：{'、'.join(BOSS_MAP.get(boss, [])) or '未知'}\n死亡時間：{kill_time}\n" + (f"備註：{note}" if note else "")

def build_join_roster_guide_flex():
    """建立歡迎加入名冊的 Flex Message"""
    return FlexSendMessage(alt_text="歡迎加入群組，請加入名冊", contents={
        "type": "bubble", "size": "mega",
        "body": {"type": "box", "layout": "vertical", "spacing": "md", "contents": [
            {"type": "text", "text": "👋 歡迎加入群組", "weight": "bold", "size": "xl", "wrap": True},
            {"type": "text", "text": "為了正確統計王表與 KPI\n請先完成名冊登記", "wrap": True, "size": "sm", "color": "#666666"},
            {"type": "separator", "margin": "lg"},
            {"type": "text", "text": "✍️ 加入名冊方式", "weight": "bold", "size": "md"},
            {"type": "box", "layout": "vertical", "spacing": "xs", "backgroundColor": "#F7F7F7", "paddingAll": "md", "cornerRadius": "md", "contents": [
                {"type": "text", "text": "加入名冊 血盟名 遊戲角色名", "size": "sm", "weight": "bold", "wrap": True},
                {"type": "text", "text": "📘 範例：加入名冊 酒窖 給你3秒逃", "size": "sm", "color": "#777777", "wrap": True}
            ]},
            {"type": "separator", "margin": "lg"},
            {"type": "text", "text": "📌 完成後即可使用王表、吃王登記等功能", "size": "xs", "color": "#999999", "wrap": True}
        ]}
    })

def build_boss_history_flex(boss_name, history):
    """建立歷史紀錄的 Carousel 卡片"""
    if not history: return TextSendMessage(text=f"❌ 查無 {boss_name} 的紀錄。")
    
    # 利用列表推導式直接生成 Carousel 內的 bubbles 陣列
    return FlexSendMessage(alt_text=f"📜 {boss_name} 歷史紀錄", contents={"type": "carousel", "contents": [{
        "type": "bubble", "size": "micro", "styles": {"header": {"separator": False}, "body": {"backgroundColor": "#FFFFFF"}},
        "header": {"type": "box", "layout": "vertical", "backgroundColor": "#464E5F", "paddingAll": "lg", "contents": [
            {"type": "text", "text": "TIME / 登記時間", "color": "#A1A7B5", "size": "xxs", "weight": "bold"},
            {"type": "text", "text": str(i.get("time", "-")), "color": "#FFFFFF", "size": "md", "weight": "bold", "margin": "sm"}
        ]},
        "body": {"type": "box", "layout": "vertical", "paddingAll": "lg", "contents": [
            {"type": "box", "layout": "horizontal", "contents": [{"type": "text", "text": "🎯", "size": "xs", "flex": 1}, {"type": "text", "text": f"{boss_name}", "size": "xs", "color": "#111111", "weight": "bold", "flex": 5}]},
            {"type": "box", "layout": "horizontal", "margin": "md", "contents": [{"type": "text", "text": "👤", "size": "xs", "flex": 1}, {"type": "text", "text": str(i.get("user", "未知")), "size": "xs", "color": "#666666", "flex": 5}]},
            {"type": "box", "layout": "vertical", "margin": "lg", "contents": [{"type": "separator", "color": "#EEEEEE"}]},
            {"type": "box", "layout": "vertical", "margin": "md", "contents": [
                {"type": "text", "text": "備註", "size": "xxs", "color": "#BBBBBB", "weight": "bold"},
                {"type": "text", "text": str(i.get("note")).strip() if i.get("note") and str(i.get("note")).strip() else "-", "size": "xs", "color": "#888888", "margin": "xs", "wrap": True, "maxLines": 3, "style": "italic"}
            ]}
        ]}
    } for i in history[:10]]})

def clear_confirm_flex():
    """清除所有王表的確認卡片"""
    return {
        "type": "bubble", "size": "mega", "styles": {"footer": {"separator": True}},
        "header": {"type": "box", "layout": "vertical", "backgroundColor": "#D32F2F", "contents": [{"type": "text", "text": "⚠️ 危險操作確認", "color": "#FFFFFF", "weight": "bold", "size": "md", "align": "center"}]},
        "body": {"type": "box", "layout": "vertical", "spacing": "md", "contents": [
            {"type": "text", "text": "清除所有王表紀錄？", "weight": "bold", "size": "md", "wrap": True, "align": "center"},
            {"type": "text", "text": "此動作將會抹除資料庫中所有現存紀錄，且「無法復原」。請再次確認您的操作。", "wrap": True, "size": "xs", "color": "#888888", "align": "center"}
        ]},
        "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
            {"type": "button", "style": "primary", "color": "#D32F2F", "height": "sm", "action": {"type": "message", "label": "確定清除", "text": "確定清除"}},
            {"type": "button", "style": "link", "color": "#444444", "height": "sm", "action": {"type": "message", "label": "取消", "text": "取消清除"}}
        ]}
    }
def build_boot_init_flex(base_time_str):
    """建立開機初始化的 Flex 字典"""
    return {"type": "bubble", "size": "mega", "body": {"type": "box", "layout": "vertical", "paddingAll": "lg", "contents": [
        {"type": "text", "text": "🔌 開機時間已紀錄", "weight": "bold", "size": "lg", "color": "#2E7D32"},
        {"type": "separator", "margin": "md", "color": "#EEEEEE"},
        {"type": "box", "layout": "vertical", "margin": "lg", "backgroundColor": "#F1F8E9", "paddingAll": "md", "cornerRadius": "md", "contents": [
            {"type": "text", "text": "🕒 開機時間", "size": "xs", "color": "#689F38", "weight": "bold"},
            {"type": "text", "text": base_time_str, "size": "md", "weight": "bold", "color": "#333333", "margin": "xs"}
        ]},
        {"type": "box", "layout": "horizontal", "margin": "md", "contents": [{"type": "text", "text": "ℹ️ 系統已自動補齊尚未登記的 CD 王", "size": "xs", "color": "#999999", "wrap": True, "flex": 1}]}
    ]}}

def build_duplicate_warning_flex(boss_name, existing_status):
    """建立重複登記警告的 Flex Message"""
    # 字典 .get() 尋找對應顏色，若找不到就回傳預設值 "#95A5A6"
    color = {"我方擊殺": "#4A90E2", "敵人吃": "#FF5252"}.get(existing_status, "#95A5A6")
    
    return FlexSendMessage(alt_text=f"重複登記提醒：{boss_name}", contents={
        "type": "bubble", "size": "kilo", "styles": {"body": {"backgroundColor": "#FFFDF5"}},
        "body": {"type": "box", "layout": "vertical", "paddingAll": "20px", "contents": [
            {"type": "text", "text": "⚠️ 晚了一步！", "weight": "bold", "color": "#FF9800", "size": "sm"},
            {"type": "text", "text": boss_name, "weight": "bold", "size": "xl", "margin": "md", "wrap": True, "color": "#333333"},
            {"type": "separator", "margin": "md"},
            {"type": "text", "text": "這隻王已經被記錄過了，請勿重複登記喔！", "size": "xs", "color": "#888888", "wrap": True, "margin": "md"},
            {"type": "box", "layout": "baseline", "margin": "md", "contents": [
                {"type": "text", "text": "已記錄為", "color": "#aaaaaa", "size": "sm", "flex": 3},
                {"type": "text", "text": existing_status, "color": color, "size": "md", "weight": "bold", "flex": 4}
            ]}
        ]}
    })

def build_auction_flex(item_name, highest_bid, bidder_name):
    """建立快閃競標的 Flex 字典"""
    return {
        "type": "bubble", "size": "mega",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": "#E67E22", "paddingAll": "sm", "contents": [{"type": "text", "text": "⚔️ 盟內裝備快閃競標", "weight": "bold", "color": "#FFFFFF", "size": "sm", "align": "center"}]},
        "body": {"type": "box", "layout": "vertical", "spacing": "md", "contents": [
            {"type": "text", "text": f"📦 物品：{item_name}", "weight": "bold", "size": "lg", "color": "#111111"},
            {"type": "separator"},
            {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
                {"type": "box", "layout": "horizontal", "contents": [{"type": "text", "text": "最高標", "size": "sm", "color": "#aaaaaa", "flex": 2}, {"type": "text", "text": f"{highest_bid} 💎", "size": "md", "weight": "bold", "color": "#E67E22", "flex": 4, "align": "end"}]},
                {"type": "box", "layout": "horizontal", "contents": [{"type": "text", "text": "領先者", "size": "sm", "color": "#aaaaaa", "flex": 2}, {"type": "text", "text": bidder_name or "目前尚無人出價", "size": "sm", "color": "#111111", "flex": 4, "align": "end", "weight": "bold"}]}
            ]}
        ]},
        "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [{"type": "text", "text": "輸入「下標 金額」參與競標", "size": "xs", "color": "#aaaaaa", "align": "center"}, {"type": "separator", "margin": "md"}]}
    }

def build_kpi_flex(title, period_text, ranking):
    """建立 KPI 統計的 Flex 字典"""
    rows = []
    for i, (name, count) in enumerate(ranking):
        # 1. 將樣式 Tuple 化，用 .get() 單行抓取顏色、粗細與圖標，找不到就給預設值
        c, w, icon = {0: ("#FFD700", "bold", "🥇"), 1: ("#C0C0C0", "bold", "🥈"), 2: ("#CD7F32", "bold", "🥉")}.get(i, ("#333333", "regular", str(i+1)))
        
        # 2. 利用 ** 字典解包，如果是前三名就動態注入背景與圓角屬性
        rows.append({"type": "box", "layout": "horizontal", "paddingAll": "sm", **({"backgroundColor": "#F8F9FA", "cornerRadius": "md", "margin": "xs"} if i < 3 else {}), "contents": [
            {"type": "text", "text": icon, "size": "sm", "flex": 1, "align": "center", "weight": w},
            {"type": "text", "text": name, "size": "sm", "flex": 4, "weight": w, "color": "#333333" if i < 3 else "#666666"},
            {"type": "text", "text": f"{count} 次", "size": "sm", "align": "end", "flex": 2, "weight": "bold", "color": c}
        ]})
        
    return {
        "type": "bubble", "size": "kilo",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": "#1A237E", "contents": [{"type": "text", "text": f"🏆 {title}", "color": "#FFFFFF", "weight": "bold", "size": "md"}]},
        "body": {"type": "box", "layout": "vertical", "spacing": "md", "contents": [{"type": "text", "text": f"📅 統計區間：{period_text}", "size": "xs", "color": "#888888"}, {"type": "box", "layout": "vertical", "spacing": "xs", "contents": rows}]}
    }

def get_welcome_flex(notion_url):
    """回傳歡迎訊息的 Flex Message 內容"""
    return {
        "type": "bubble", "size": "mega",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": "#05B050", "contents": [{"type": "text", "text": "天堂M吃王小幫手", "weight": "bold", "color": "#FFFFFF", "size": "sm"}]},
        "body": {"type": "box", "layout": "vertical", "contents": [
            {"type": "text", "text": "感謝邀請！", "weight": "bold", "size": "xl", "margin": "md"},
            {"type": "text", "text": "本群組已自動開啟 7 天試用期。", "size": "sm", "color": "#666666", "wrap": True},
            {"type": "separator", "margin": "lg"},
            {"type": "text", "text": "點擊下方按鈕查看如何快速上手：", "size": "sm", "color": "#999999", "margin": "md", "wrap": True}
        ]},
        "footer": {"type": "box", "layout": "vertical", "contents": [
            {"type": "button", "style": "primary", "color": "#05B050", "action": {"type": "uri", "label": "📖 完整使用教學", "uri": notion_url}}
        ]}
    }

def build_roster_added_flex(clan, game_name):
    """回傳名冊登記成功的 Flex 字典"""
    return {
        "type": "bubble", "size": "mega", "styles": {"body": {"cornerRadius": "md"}},
        "body": {"type": "box", "layout": "vertical", "backgroundColor": "#FFFFFF", "paddingAll": "lg", "contents": [
            {"type": "box", "layout": "horizontal", "contents": [{"type": "text", "text": "✅", "size": "lg", "flex": 0}, {"type": "text", "text": "登記成功", "weight": "bold", "size": "md", "color": "#2E7D32", "margin": "md", "flex": 1}]},
            {"type": "separator", "margin": "lg", "color": "#EEEEEE"},
            {"type": "box", "layout": "vertical", "margin": "lg", "spacing": "sm", "contents": [
                {"type": "box", "layout": "horizontal", "contents": [{"type": "text", "text": "遊戲角色", "size": "xs", "color": "#888888", "flex": 3}, {"type": "text", "text": game_name, "size": "sm", "color": "#333333", "weight": "bold", "flex": 7, "align": "end"}]},
                {"type": "box", "layout": "horizontal", "contents": [{"type": "text", "text": "所屬血盟", "size": "xs", "color": "#888888", "flex": 3}, {"type": "text", "text": clan, "size": "sm", "color": "#333333", "weight": "bold", "flex": 7, "align": "end"}]}
            ]},
            {"type": "text", "text": "您現在可以正常使用王表功能了", "size": "xxs", "color": "#AAAAAA", "margin": "xl", "align": "center"}
        ]}
    }
def build_roster_confirm_update_flex(old_name, old_clan, new_name, new_clan):
    """建立確認更新名冊的 Flex 字典"""
    return {
        "type": "bubble", "size": "mega", "styles": {"footer": {"separator": True}},
        "header": {"type": "box", "layout": "vertical", "paddingBottom": "none", "contents": [{"type": "text", "text": "確認更新資料", "weight": "bold", "color": "#E67E22", "size": "lg"}]},
        "body": {"type": "box", "layout": "vertical", "contents": [
            {"type": "text", "text": "系統偵測到該名冊已存在，是否要覆蓋現有資訊？", "wrap": True, "size": "sm", "color": "#8c8c8c"},
            {"type": "separator", "margin": "lg"},
            {"type": "box", "layout": "vertical", "margin": "lg", "spacing": "sm", "contents": [
                {"type": "box", "layout": "horizontal", "contents": [{"type": "text", "text": "目前內容", "size": "sm", "color": "#aaaaaa", "flex": 2}, {"type": "text", "text": f"{old_name} / {old_clan}", "size": "sm", "color": "#666666", "flex": 4, "align": "end"}]},
                {"type": "box", "layout": "horizontal", "contents": [{"type": "text", "text": "修改為", "size": "sm", "color": "#1DB446", "flex": 2, "weight": "bold"}, {"type": "text", "text": f"{new_name} / {new_clan}", "size": "sm", "color": "#1DB446", "flex": 4, "align": "end", "weight": "bold"}]}
            ]}
        ]},
        "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
            {"type": "button", "style": "primary", "height": "sm", "color": "#1DB446", "action": {"type": "message", "label": "確認修改", "text": "確認修改"}},
            {"type": "button", "style": "secondary", "height": "sm", "action": {"type": "message", "label": "取消操作", "text": "取消"}}
        ]}
    }

def build_roster_self_flex(game_name, clan):
    """建立查看個人名冊的 Flex 字典"""
    return {
        "type": "bubble", "size": "mega", "styles": {"footer": {"separator": True}},
        "header": {"type": "box", "layout": "vertical", "backgroundColor": "#273132", "paddingTop": "15px", "paddingBottom": "15px", "contents": [
            {"type": "text", "text": "MY ROSTER", "color": "#ffffff66", "size": "xs", "weight": "bold", "letterSpacing": "2px"},
            {"type": "text", "text": "👤 我的個人名冊", "color": "#ffffff", "size": "lg", "weight": "bold"}
        ]},
        "body": {"type": "box", "layout": "vertical", "contents": [
            {"type": "box", "layout": "horizontal", "margin": "md", "contents": [{"type": "text", "text": "遊戲名字", "color": "#8c8c8c", "size": "sm", "flex": 1}, {"type": "text", "text": game_name, "color": "#111111", "size": "sm", "flex": 2, "weight": "bold", "align": "end"}]},
            {"type": "separator", "margin": "md"},
            {"type": "box", "layout": "horizontal", "margin": "md", "contents": [{"type": "text", "text": "血盟", "color": "#8c8c8c", "size": "sm", "flex": 1}, {"type": "text", "text": clan, "color": "#111111", "size": "sm", "flex": 2, "weight": "bold", "align": "end"}]}
        ]},
        "footer": {"type": "box", "layout": "vertical", "paddingTop": "10px", "contents": [{"type": "text", "text": "我的名冊", "size": "xs", "color": "#aaaaaa", "align": "center"}]}
    }
def build_stats_report_flex(total, stats_dict, details):
    """建立固定王統計報表 Flex Message"""
    
    # 1. 基礎狀態統計列 (利用列表推導式合併)
    status_rows = [{"type": "box", "layout": "horizontal", "margin": m, "contents": [
        {"type": "text", "text": label, "size": "sm", "color": color, "weight": "bold", "flex": 3},
        {"type": "text", "text": f"{stats_dict.get(key, 0)} 次", "align": "end", "weight": "bold", "color": "#333333", "flex": 2}
    ]} for key, label, color, m in [("我方擊殺", "🟢 我方擊殺", "#4A90E2", "lg"), ("敵人吃", "🔴 敵人吃掉", "#FF5252", "md"), ("漏掉", "⚪ 漏掉未吃", "#95A5A6", "md")]]
    
    # 2. 動態生成明細區塊的小函式 (DRY: Don't Repeat Yourself)
    def make_detail(key, label, color):
        if not (times := details.get(key)): return []
        return [{"type": "separator", "margin": "lg"}, 
                {"type": "text", "text": label, "size": "xs", "color": color, "weight": "bold", "margin": "md"},
                {"type": "text", "text": "\n".join(f"• {t}" for t in times), "size": "xs", "color": "#666666", "wrap": True, "margin": "sm"}]

    # 3. 組合 Body 內容並回傳 Flex
    return FlexSendMessage(alt_text="固定王統計報表", contents={
        "type": "bubble", "size": "mega",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": "#2C3E50", "paddingAll": "15px", "contents": [{"type": "text", "text": "📊 固定王統計報表", "color": "#ffffff", "weight": "bold", "size": "md", "align": "center"}]},
        "body": {"type": "box", "layout": "vertical", "paddingAll": "20px", "contents": [
            {"type": "box", "layout": "horizontal", "alignItems": "center", "contents": [
                {"type": "text", "text": "總計出現次數", "color": "#888888", "size": "sm", "flex": 2},
                {"type": "text", "text": f"{total} 次", "size": "xl", "weight": "bold", "color": "#333333", "align": "end", "flex": 2}
            ]},
            {"type": "separator", "margin": "lg"}
        ] + status_rows + make_detail("敵人吃", "⚠️ 敵人吃場次明細：", "#FF5252") + make_detail("漏掉", "⚠️ 漏掉場次明細：", "#95A5A6")}
    })

def build_roster_delete_confirm_flex(game_name):
    """建立名冊刪除確認的 Flex 字典"""
    return {
        "type": "bubble", "size": "mega",
        "header": {"type": "box", "layout": "vertical", "paddingBottom": "none", "contents": [{"type": "text", "text": "⚠️ 刪除確認", "weight": "bold", "color": "#E74C3C", "size": "lg"}]},
        "body": {"type": "box", "layout": "vertical", "contents": [
            {"type": "text", "text": "確定要從系統中移除此角色嗎？此動作無法復原。", "wrap": True, "size": "sm", "color": "#666666"},
            {"type": "box", "layout": "vertical", "margin": "lg", "backgroundColor": "#FDF2F2", "paddingAll": "md", "cornerRadius": "sm", "contents": [
                {"type": "box", "layout": "horizontal", "contents": [{"type": "text", "text": "待刪除角色", "size": "sm", "color": "#888888", "flex": 3}, {"type": "text", "text": f"{game_name}", "size": "sm", "color": "#E74C3C", "flex": 4, "align": "end", "weight": "bold"}]}
            ]}
        ]},
        "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
            {"type": "button", "style": "primary", "color": "#E74C3C", "height": "sm", "action": {"type": "message", "label": "確認刪除", "text": "確認刪除"}},
            {"type": "button", "style": "secondary", "height": "sm", "action": {"type": "message", "label": "取消", "text": "取消"}}
        ]}
    }

def build_error_flex(title, message, boss_name):
    """生成高質感警示類型 Flex Message"""
    return FlexSendMessage(alt_text=f"錯誤通知: {title}", contents={
        "type": "bubble", "size": "mega",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": "#FF5252", "paddingAll": "20px", "contents": [{"type": "text", "text": f"⚠️ {title}", "weight": "bold", "color": "#FFFFFF", "size": "lg"}]},
        "body": {"type": "box", "layout": "vertical", "spacing": "md", "paddingAll": "20px", "contents": [
            {"type": "box", "layout": "horizontal", "contents": [{"type": "text", "text": "目標對象", "size": "sm", "color": "#aaaaaa", "flex": 2}, {"type": "text", "text": boss_name, "weight": "bold", "size": "sm", "color": "#333333", "flex": 4, "align": "end"}]},
            {"type": "separator", "margin": "md"},
            {"type": "box", "layout": "vertical", "margin": "lg", "paddingAll": "12px", "backgroundColor": "#F8F9FA", "cornerRadius": "md", "contents": [
                {"type": "text", "text": "詳細資訊", "size": "xxs", "color": "#888888", "margin": "none"},
                {"type": "text", "text": message, "wrap": True, "color": "#E63946", "size": "sm", "margin": "sm", "weight": "bold"}
            ]}
        ]},
        "footer": {"type": "box", "layout": "vertical", "paddingBottom": "15px", "contents": [{"type": "text", "text": "請確認後重新輸入", "size": "xs", "color": "#aaaaaa", "align": "center"}]}
    })

def build_roster_deleted_flex():
    """名冊已成功刪除的提示卡片 (字典回傳)"""
    return {
        "type": "bubble", "size": "mega",
        "body": {"type": "box", "layout": "vertical", "contents": [{"type": "box", "layout": "horizontal", "spacing": "md", "alignItems": "center", "contents": [
            {"type": "text", "text": "🗑", "size": "xl", "flex": 0},
            {"type": "box", "layout": "vertical", "contents": [{"type": "text", "text": "名冊已成功刪除", "weight": "bold", "size": "md", "color": "#555555"}, {"type": "text", "text": "該角色資訊已從資料庫移除", "size": "xs", "color": "#aaaaaa"}]}
        ]}]}
    }

def build_roster_search_flex(keyword, rows):
    """建立名冊查詢結果的 Flex Message"""
    # 1. 列表推導式直接生出所有搜尋結果區塊，若無結果則提供預設文字
    contents = [{"type": "box", "layout": "vertical", "spacing": "xs", "margin": "md", "contents": [
        {"type": "text", "text": f"🎮 角色：{game_name}", "size": "sm", "weight": "bold"},
        {"type": "text", "text": f"🏰 血盟：{clan_name}", "size": "sm", "weight": "bold"},
        {"type": "text", "text": f"📱 LINE名稱：{line_name}", "size": "sm", "weight": "bold"}
    ]} for game_name, clan_name, line_name in rows] or [{"type": "text", "text": "查無符合的名冊資料", "size": "sm", "color": "#888888"}]
    
    # 2. 回傳 Flex
    return FlexSendMessage(alt_text=f"名冊查詢：{keyword}", contents={
        "type": "bubble", "size": "mega",
        "header": {"type": "box", "layout": "vertical", "contents": [{"type": "text", "text": f"🔍 名冊查詢：{keyword}", "weight": "bold", "size": "lg"}]},
        "body": {"type": "box", "layout": "vertical", "contents": contents}
    })

def ensure_roster_table():
    """確保名冊資料表存在"""
    if conn := get_pg_conn():
        with conn, conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS roster (
                id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, line_user_id TEXT NOT NULL,
                game_name TEXT NOT NULL, clan_name TEXT NOT NULL, line_name TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (line_user_id, game_name));""")

def ensure_shift_table():
    """確保交班資料表存在"""
    if conn := get_pg_conn():
        with conn, conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS shift_info (group_id TEXT PRIMARY KEY, current_user_id TEXT, next_user_id TEXT, updated_at TIMESTAMPTZ DEFAULT NOW());")

# 在啟動區（如 init_db 附近）執行一次
ensure_shift_table()

def get_line_display_name(user_id):
    """取得 LINE 用戶顯示名稱"""
    try: return line_bot_api.get_profile(user_id).display_name
    except: return None

def query_roster(clan_name=None):
    """查詢名冊 (支援特定血盟或全部)"""
    if conn := get_pg_conn():
        with conn, conn.cursor() as cur:
            # 條件式 SQL 字串組合與參數傳遞
            cur.execute("SELECT game_name, clan_name, COALESCE(line_name, '') FROM roster " + ("WHERE clan_name = %s ORDER BY created_at" if clan_name else "ORDER BY clan_name, created_at"), (clan_name,) if clan_name else ())
            return cur.fetchall()

def search_roster(keyword):
    """模糊搜尋名冊"""
    if conn := get_pg_conn():
        with conn, conn.cursor() as cur:
            # 利用 Tuple 乘法 (f"%{keyword}%",)*3 瞬間填補 3 個參數
            cur.execute("SELECT game_name, clan_name, COALESCE(line_name, '') FROM roster WHERE game_name ILIKE %s OR clan_name ILIKE %s OR line_name ILIKE %s ORDER BY clan_name, game_name", (f"%{keyword}%",)*3)
            return cur.fetchall()

def build_boss_list_text():
    """建立王列表 (含簡稱)"""
    return "\n".join(["📜【王列表（含所有簡稱）】\n"] + [f"🔹 {boss}\n   ➜ {'、'.join(aliases)}\n" for boss, aliases in alias_map.items()])

def build_boss_cd_list_text():
    """建立王重生時間一覽"""
    # 利用 cd % 1 抓取小數點，並搭配行內 f-string 直接判斷是否需要加上 "X 分"
    return "\n".join(["⏳【王重生時間一覽】\n"] + [f"🔹 {boss}：{int(cd)} 小時{f' {int((cd % 1) * 60)} 分' if cd % 1 else ''}" for boss, cd in sorted(cd_map.items(), key=lambda x: x[1])])

def get_status_flex(status_text, expiry_date, days_left):
    """回傳群組狀態的 Flex Message 內容"""
    color = "#E63946" if days_left < 3 else "#1DB954"
    return {
        "type": "bubble", "size": "mega",
        "body": {"type": "box", "layout": "vertical", "contents": [
            {"type": "text", "text": "🛡️ 群組權限狀態", "weight": "bold", "color": "#1DB954", "size": "sm"},
            {"type": "text", "text": "🟢 服務中", "weight": "bold", "size": "xxl", "margin": "md"},
            {"type": "separator", "margin": "lg", "backgroundColor": "#EEEEEE"},
            {"type": "box", "layout": "vertical", "margin": "lg", "spacing": "sm", "contents": [
                {"type": "box", "layout": "baseline", "spacing": "sm", "contents": [{"type": "text", "text": "目前權限", "color": "#aaaaaa", "size": "sm", "flex": 2}, {"type": "text", "text": status_text, "wrap": True, "color": "#666666", "size": "sm", "flex": 5}]},
                {"type": "box", "layout": "baseline", "spacing": "sm", "contents": [{"type": "text", "text": "到期日期", "color": "#aaaaaa", "size": "sm", "flex": 2}, {"type": "text", "text": expiry_date, "wrap": True, "color": "#666666", "size": "sm", "flex": 5}]},
                {"type": "box", "layout": "baseline", "spacing": "sm", "contents": [{"type": "text", "text": "剩餘天數", "color": "#aaaaaa", "size": "sm", "flex": 2}, {"type": "text", "text": f"{days_left} 天", "wrap": True, "color": color, "size": "sm", "flex": 5, "weight": "bold"}]}
            ]}
        ]},
        "footer": {"type": "box", "layout": "vertical", "contents": [{"type": "button", "style": "link", "height": "sm", "action": {"type": "uri", "label": "了解續約方案", "uri": "https://line.me/ti/p/wenhao0222"}}]}
    }

def get_delete_result_flex(success, name_input, final_name=None):
    """回傳刪除操作結果的 Flex Message 內容"""
    # 利用 Tuple 賦值，一行搞定四個變數的 if/else 判斷
    c, t, d, icon = ("#E63946", "🗑 已成功清除", f"【{final_name}】的相關紀錄已從系統中移除。", "https://cdn-icons-png.flaticon.com/512/1214/1214428.png") if success else ("#AAAAAA", "❌ 找不到紀錄", f"系統中找不到與「{name_input}」相符的資料。", "https://cdn-icons-png.flaticon.com/512/564/564619.png")
    
    return {
        "type": "bubble", "size": "kilo",
        "body": {"type": "box", "layout": "vertical", "spacing": "md", "contents": [
            {"type": "image", "url": icon, "size": "xxs", "aspectMode": "fit"},
            {"type": "text", "text": t, "weight": "bold", "size": "lg", "align": "center", "color": c},
            {"type": "text", "text": d, "size": "sm", "color": "#666666", "wrap": True, "align": "center"}
        ]}
    }

def build_roster_flex(rows):
    """建立名冊資料表 Flex Message (斑馬紋設計)"""
    return FlexSendMessage(alt_text="📖 名冊資料", contents={
        "type": "bubble", "size": "mega",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": "#F4F4F4", "paddingAll": "12px", "contents": [{"type": "text", "text": "📖 名冊資料", "weight": "bold", "size": "md", "color": "#444444"}]},
        "body": {"type": "box", "layout": "vertical", "spacing": "none", "paddingAll": "0px", "contents": [
            {"type": "box", "layout": "horizontal", "paddingAll": "8px", "backgroundColor": "#333333", "contents": [
                {"type": "text", "text": "角色", "flex": 3, "size": "xs", "color": "#FFFFFF", "weight": "bold"},
                {"type": "text", "text": "血盟", "flex": 2, "size": "xs", "color": "#FFFFFF", "weight": "bold", "align": "center"},
                {"type": "text", "text": "LINE", "flex": 2, "size": "xs", "color": "#FFFFFF", "weight": "bold", "align": "end"}
            ]}
        ] + [
            # 列表推導式處理資料列與斑馬紋背景
            {"type": "box", "layout": "horizontal", "paddingAll": "10px", "backgroundColor": "#F9F9F9" if i % 2 else "#FFFFFF", "contents": [
                {"type": "text", "text": game_name, "flex": 3, "size": "sm", "weight": "bold", "wrap": True, "color": "#111111"},
                {"type": "text", "text": clan_name or "-", "flex": 2, "size": "xs", "align": "center", "color": "#666666", "margin": "sm"},
                {"type": "text", "text": line_name or "-", "flex": 2, "size": "xs", "align": "end", "color": "#1E90FF"}
            ]} for i, (game_name, line_name, clan_name) in enumerate(rows)
        ] + [
            {"type": "box", "layout": "vertical", "margin": "md", "contents": [{"type": "separator", "color": "#EEEEEE"}, {"type": "text", "text": "💡 資料有誤請連繫 @H. 進行修正", "size": "xxs", "color": "#AAAAAA", "align": "center", "margin": "md"}]}
        ]}
    })

def build_shift_status_flex(group_id, current_uid, next_uid):
    """建立交接班狀態確認 Flex Message"""
    # Tuple 多變數條件賦值
    n_display, n_color, n_weight = (get_username(next_uid), "#555555", "bold") if next_uid else ("⚠️ 尚無人接班", "#FF5252", "bold")
    
    return FlexSendMessage(alt_text="📢 交接班狀態確認", contents={
        "type": "bubble", "styles": {"footer": {"separator": True}},
        "header": {"type": "box", "layout": "vertical", "backgroundColor": "#2C3E50", "paddingAll": "20px", "contents": [{"type": "text", "text": "⚔️ 王表交接系統", "color": "#FFFFFF", "weight": "bold", "size": "lg", "align": "center"}]},
        "body": {"type": "box", "layout": "vertical", "spacing": "xl", "paddingAll": "20px", "contents": [
            {"type": "box", "layout": "vertical", "contents": [
                {"type": "text", "text": "當前值班員", "size": "xs", "color": "#aaaaaa"},
                {"type": "box", "layout": "horizontal", "alignItems": "center", "margin": "sm", "contents": [
                    {"type": "text", "text": "●", "size": "xs", "color": "#66BB6A" if current_uid else "#FF5252", "flex": 0},
                    {"type": "text", "text": get_username(current_uid) if current_uid else "目前空班中", "weight": "bold", "size": "md", "margin": "md", "flex": 1}
                ]}
            ]},
            {"type": "separator"},
            {"type": "box", "layout": "vertical", "contents": [
                {"type": "text", "text": "預計接班人員", "size": "xs", "color": "#aaaaaa"},
                {"type": "text", "text": n_display, "color": n_color, "weight": n_weight, "size": "md", "margin": "sm"}
            ]}
        ]},
        "footer": {"type": "box", "layout": "vertical", "paddingAll": "15px", "contents": [{"type": "button", "style": "primary", "color": "#1DB100", "height": "sm", "action": {"type": "message", "label": "🙋 我要接班", "text": "接班"}}]}
    })
def get_boss_history(group_id, name):
    """查詢該群組、該王的最近 5 筆擊殺紀錄"""
    if not (conn := get_pg_conn()): return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT kill_time, user_id, note FROM boss_time WHERE group_id = %s AND boss_name = %s ORDER BY kill_time DESC LIMIT 5", (group_id, name))
            # 建立時區轉換 Lambda 並配合推導式產出結果
            to_tz = lambda t: t.astimezone(TZ) if t.tzinfo else pytz.utc.localize(t).astimezone(TZ)
            return [{"time": to_tz(r[0]).strftime("%m/%d %H:%M"), "user": get_username(r[1]), "note": r[2] or ""} for r in cur.fetchall()]
    except Exception as e: return print(f"History Error: {e}") or []
    finally: conn.close()

def build_record_success_flex(name, status):
    """建立紀錄成功的 Flex Message (利用對映表簡化顏色與圖示判斷)"""
    icon, color = {"我方擊殺": ("✅", "#4A90E2"), "敵人吃": ("☠️", "#FF5252")}.get(status, ("⚠️", "#95A5A6"))
    return FlexSendMessage(alt_text=f"紀錄成功：{name} {status}", contents={
        "type": "bubble", "size": "kilo", "styles": {"body": {"backgroundColor": "#FAFAFA"}},
        "body": {"type": "box", "layout": "vertical", "paddingAll": "20px", "contents": [
            {"type": "text", "text": f"{icon} 紀錄成功", "weight": "bold", "color": "#888888", "size": "xs"},
            {"type": "text", "text": name, "weight": "bold", "size": "xl", "margin": "md", "wrap": True},
            {"type": "separator", "margin": "md"},
            {"type": "box", "layout": "baseline", "margin": "md", "contents": [{"type": "text", "text": "狀態", "color": "#aaaaaa", "size": "sm", "flex": 2}, {"type": "text", "text": status, "color": color, "size": "md", "weight": "bold", "flex": 5}]}]}})

def build_shift_success_flex(name):
    """建立接班成功的 Flex Message"""
    return FlexSendMessage(alt_text="接班成功", contents={"type": "bubble", "size": "kilo", "body": {"type": "box", "layout": "vertical", "paddingAll": "lg", "contents": [
        {"type": "text", "text": "✅ 接班登記成功", "weight": "bold", "color": "#2E7D32"},
        {"type": "text", "text": f"下一班人員：{name}", "margin": "md", "size": "sm"},
        {"type": "separator", "margin": "md"},
        {"type": "text", "text": "💡 交班請輸入 @All 交班", "margin": "md", "size": "xs", "color": "#888888"}]}})

# 王資料
alias_map = {
    "四色": ["四色", "76", "4", "四", "4色","c","C"],
    "小紅": ["小紅", "55", "紅", "R", "r"],
    "小綠": ["小綠", "54", "綠", "G", "g"],
    "守護螞蟻": ["守護螞蟻", "螞蟻", "29", "ant", "a", "A"],
    "巨大蜈蚣": ["巨大蜈蚣", "蜈蚣", "海4", "海蟲", "6", "06"],
    "86左飛龍": ["左飛龍", "861", "86左飛龍", "左", "86下"],
    "86右飛龍": ["右飛龍", "862", "86右飛龍", "右", "86上"],
    "伊弗利特": ["伊弗利特", "伊弗", "EF", "ef", "伊佛", "衣服", "E", "e", "Ef", "eF"],
    "大腳瑪幽": ["大腳瑪幽", "大腳", "69", "F", "f"],
    "巨大飛龍": ["巨大飛龍", "巨飛", "GF", "82", "gf"],
    "83中飛龍": ["中飛龍", "中", "中央龍", "83", "83中飛龍"],
    "85東飛龍": ["東飛龍", "東", "85飛龍", "85","85東飛龍"],
    "大黑長者": ["大黑長者", "大黑", "黑", "863","b","B"],
    "力卡溫": ["力卡溫", "狼人", "狼王", "22", "狼", "W", "w"],
    "卡司特王": ["卡司特", "卡", "卡王", "25", "卡司特王"],
    "史前巨鱷": ["巨大鱷魚", "鱷魚", "51", "史前巨鱷"],
    "強盜頭目": ["強盜頭目", "強盜", "32"],
    "樹精": ["樹精", "樹", "24","t","T"],
    "蜘蛛": ["蜘蛛", "D", "喇牙", "39", "d"],
    "變形怪首領": ["變形怪首領", "變形怪", "變怪", "68", "變王"],
    "古代巨人": ["古代巨人", "古巨", "巨人", "78"],
    "不死鳥": ["不死鳥", "鳥", "452", "gg", "GG"],
    "死亡騎士": ["死亡騎士", "死騎", "05", "5"],
    "克特": ["克特", "12"],
    "賽尼斯的分身": ["賽尼斯的分身", "賽尼斯", "304"],
    "貝里斯": ["貝里斯", "大克特", "將軍", "81"],
    "烏勒庫斯": ["烏勒庫斯", "烏", "23"],
    "奈克偌斯": ["奈克偌斯", "奈", "57"],
}
cd_map = {
    "四色": 2, "小紅": 2, "小綠": 2, "守護螞蟻": 3.5, "巨大蜈蚣": 2,
    "86左飛龍": 2, "86右飛龍": 2, "伊弗利特": 2, "大腳瑪幽": 3,
    "巨大飛龍": 6, "83中飛龍": 3, "85東飛龍": 3, "大黑長者": 3,
    "力卡溫": 8, "卡司特王": 7.5, "史前巨鱷": 3, "強盜頭目": 3,
    "樹精": 3, "蜘蛛": 4, "變形怪首領": 7, "古代巨人": 8.5,
    "不死鳥": 8, "死亡騎士": 4, "克特": 10,
    "賽尼斯的分身": 3, "貝里斯": 6, "烏勒庫斯": 6,
    "奈克偌斯": 4,
}
BOSS_MAP = {
    "四色": ["76"],
    "小紅": ["55"],
    "小綠": ["54"],
    "守護螞蟻": ["29"],
    "巨大蜈蚣": ["06"],
    "86左飛龍": ["86"],
    "86右飛龍": ["86"],
    "伊弗利特": ["45"],
    "大腳瑪幽": ["69"],
    "巨大飛龍": ["82、86"],
    "83中飛龍": ["83"],
    "85東飛龍": ["85"],
    "大黑長者": ["86"],
    "力卡溫": ["22"],
    "卡司特王": ["25"],
    "史前巨鱷": ["51"],
    "強盜頭目": ["32"],
    "樹精": ["23、24、57"],
    "蜘蛛": ["39,65"],
    "變形怪首領": ["68"],
    "古代巨人": ["78"],
    "不死鳥": ["45"],
    "死亡騎士": ["05"],
    "克特": ["12"],
    "賽尼斯的分身": ["81"],
    "貝里斯": ["81"],
    "烏勒庫斯": ["23"],
    "奈克偌斯": ["57"],
}
fixed_bosses = {
     "奇岩一樓王": {
        "times": ["00:00", "06:00", "12:00", "18:00"],
        "weekdays": [0, 1, 2, 3, 4]  # 週一～週五
    },"奇岩二樓王": {
        "times": ["07:00", "14:00", "21:00"],
        "weekdays": [0, 1, 2, 3, 4]
    },"奇岩三樓王": {
        "times": ["20:15"],
        "weekdays": [0, 1, 2, 3, 4]
    },"奇岩四樓王": {
        "times": ["21:15"],
        "weekdays": [0, 1, 2, 3, 4]
    },"黑暗四樓王": {
        "times": ["00:00", "18:00"]
    },"三王": {
        "times": ["19:15"]
    },"惡魔": {
        "times": ["22:00"]
    },"巴風特": {
        "times": ["14:00", "20:00"]
    },"異界炎魔": {
        "times": ["23:00"]
    },"烈焰大死騎": {
        "times": ["23:30"]
    },"涅默西斯高輪": {
        "times": ["22:30"]
    },"魔法師": {
        "times": ["01:00","03:00","05:00","07:00","09:00","11:00",
                  "13:00","15:00","17:00","19:00","21:00","23:00"]
    }
}
def get_real_boss_name(input_name):
    """將使用者輸入的簡稱，轉換為 alias_map 中的正式名稱"""
    return next((formal for formal, aliases in alias_map.items() if input_name in aliases), input_name)

def get_roster_profile(user_id):
    """取得名冊資料字典 (若無則回傳 None)"""
    return {"name": r[0], "clan": r[1], "line_name": r[2]} if (r := roster_get_by_user(user_id)) else None

def get_boss(name):
    """尋找王名 (類似 get_real_boss_name 但找不到回傳 None)"""
    return next((boss for boss, aliases in alias_map.items() if name in aliases), None)

def parse_time(token):
    """解析使用者輸入的時間字串"""
    now = now_tw()
    if token in ("6", "6666", "K", "k"): return now
    
    try:
        # 利用 datetime.strptime 處理 4 碼或 6 碼字串，省去手動切片與範圍檢查
        fmt = "%H%M" if len(token) == 4 else "%H%M%S" if len(token) == 6 else None
        if not fmt: return None
        
        t = now.replace(hour=(dt := datetime.strptime(token, fmt)).hour, minute=dt.minute, second=dt.second)
        return t - timedelta(days=1) if t > now else t
    except ValueError:
        return None
    
def get_next_fixed_time(time_list):
    """取得下一個固定時間 (若今天已過則取明天第一個)"""
    now, today = now_tw(), now_tw().date()
    valid = [dt for t in time_list if (dt := TZ.localize(datetime.strptime(f"{today} {t}", "%Y-%m-%d %H:%M"))) >= now]
    return min(valid) if valid else TZ.localize(datetime.strptime(f"{today + timedelta(days=1)} {time_list[0]}", "%Y-%m-%d %H:%M"))

def get_next_fixed_time_fixed(boss_conf):
    """取得下一個符合特定星期與時間的排程 (最多找一週)"""
    now, today = now_tw(), now_tw().date()
    for d in range(8):
        current = today + timedelta(days=d)
        if "weekdays" in boss_conf and current.weekday() not in boss_conf["weekdays"]: continue
        if valid := [dt for t in boss_conf["times"] if (dt := TZ.localize(datetime.strptime(f"{current} {t}", "%Y-%m-%d %H:%M"))) >= now]:
            return min(valid)
    return None

def init_cd_boss_with_given_time(group_id, base_time, user_id, cd_map):   
    """開機初始化：批量將沒紀錄的王寫入 PostgreSQL"""
    if not (conn := get_pg_conn()): return print("無法取得資料庫連線")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT boss_name FROM boss_time WHERE group_id = %s", (group_id,))
            recorded = {r[0] for r in cur.fetchall()}
            
            # 使用列表推導式一次性準備好所有需要 insert 的資料
            to_insert = [(group_id, boss, base_time, base_time + timedelta(hours=cd), user_id, "🔌開機", "boot") for boss, cd in cd_map.items() if boss not in recorded]
            
            if to_insert:
                # 使用 executemany 進行高效能的批量寫入
                cur.executemany("INSERT INTO boss_time (group_id, boss_name, kill_time, respawn_time, user_id, note, source) VALUES (%s, %s, %s, %s, %s, %s, %s)", to_insert)
            
            conn.commit()
            print(f"成功將 {len(to_insert)} 筆開機紀錄寫入 PostgreSQL 資料庫！")
    except Exception as e: 
        conn.rollback(); print(f"寫入資料庫發生錯誤: {e}")
    finally: conn.close()

def handle_boss_skipped(event, group_id, boss_name, user_id, note):
    """處理輪空登記 (包含時間阻擋與防呆)"""
    if not (cd := cd_map.get(boss_name)): return
    if boss_name not in (records := get_latest_boss_records(group_id)):
        return safe_reply(event, f"❌ 找不到【{boss_name}】歷史紀錄，請先用一般擊殺建立基準。", None)

    # 時區轉換與時間基準建立
    base = datetime.fromisoformat(records[boss_name][0]["respawn"])
    base_time = (base.astimezone(TZ) if base.tzinfo else pytz.utc.localize(base).astimezone(TZ))
    now = now_tw()

    # 防呆檢查 (利用單行指派，如果不符合條件才建立錯誤卡片)
    if (msg := f"還沒重生喔！\n預計重生為：{base_time.strftime('%H:%M')}" if now < base_time - timedelta(minutes=5) else 
                 f"已逾時超過 15 分鐘。\n請在擊殺後改用「一般擊殺」校正。" if now > base_time + timedelta(minutes=15) else None):
        title = "⚠️ 登記失敗：冷卻中" if "重生" in msg else "⚠️ 登記失敗：已逾時"
        return safe_reply(event, f"⚠️ {title}\n{msg}", build_error_flex(title, msg, boss_name))

    # 資料儲存與成功回覆
    new_respawn = base_time + timedelta(hours=cd)
    save_boss_to_pg(group_id, boss_name, base_time, new_respawn, user_id, note, source="skip")
    
    kt, rt = base_time.strftime("%H:%M:%S"), new_respawn.strftime("%H:%M:%S")
    safe_reply(event, f"⭕ 輪空成功：{boss_name}\n基準點：{kt}\n下趟：{rt}", build_register_boss_flex(boss_name, kt, rt, get_username(user_id), note, is_skip=True))

def get_kpi_range(now):
    """計算 KPI 區間 (本週三 09:00 至下週三 09:00)"""
    # 直接計算並回退到最近的週三 09:00
    start = now.replace(hour=9, minute=0, second=0, microsecond=0) - timedelta(days=(now.weekday() - 2) % 7)
    # 若計算出的週三 09:00 大於現在時間，代表目前還在「上個週期」的尾端 (例如週三早上 8 點)
    if start > now: start -= timedelta(days=7)
    return start, start + timedelta(days=7)

def calculate_kpi(boss_db, start, end):
    """計算 KPI，排除系統開機與備份資料，並透過 (user, boss, time) 去重"""
    result, seen = {}, set()
    for boss, records in boss_db.items():
        for r in records:
            if r.get("user") == "__SYSTEM__" or r.get("source") == "backup": continue
            if start <= (dt := TZ.localize(datetime.strptime(f"{r['date']} {r['kill']}", "%Y-%m-%d %H:%M:%S"))) < end:
                if (key := (r["user"], boss, dt)) not in seen:
                    seen.add(key); result[r["user"]] = result.get(r["user"], 0) + 1
    return result

def get_pg_conn():
    """解析 DATABASE_URL 並回傳 psycopg2 連線"""
    if not (url := os.environ.get("DATABASE_URL")): raise RuntimeError("DATABASE_URL not set")
    res = urlparse(url)
    return psycopg2.connect(host=res.hostname, port=res.port, user=res.username, password=res.password, dbname=res.path[1:], sslmode="require")

def roster_get_by_user(user_id):
    """根據 LINE UID 取得單一名冊資料"""
    with get_pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT game_name, clan_name, line_name FROM roster WHERE line_user_id = %s ORDER BY updated_at DESC LIMIT 1", (user_id,))
        return cur.fetchone()

def roster_insert(user_id, game_name, clan_name, line_name):
    """新增名冊資料"""
    with get_pg_conn() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO roster (line_user_id, line_name, game_name, clan_name) VALUES (%s, %s, %s, %s)", (user_id, line_name, game_name, clan_name))

def roster_update(user_id, game_name, clan_name):
    """更新名冊資料 (同時更新 LINE 名稱)"""
    with get_pg_conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE roster SET game_name=%s, clan_name=%s, line_name=%s, updated_at=NOW() WHERE line_user_id=%s", (game_name, clan_name, get_line_display_name(user_id), user_id))

def roster_delete(user_id):
    """刪除名冊資料"""
    with get_pg_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM roster WHERE line_user_id = %s", (user_id,))

# FastAPI Webhook
@app.on_event("startup")
async def startup(): ensure_roster_table() # 若有需要可加回 asyncio.create_task(boss_reminder_loop())

@app.post("/callback")
async def callback(request: Request, x_line_signature: str = Header(None)):
    await process_line_event(await request.body(), x_line_signature); return "OK"

async def process_line_event(body: bytes, signature: str):
    try: handler.handle(body.decode("utf-8"), signature)
    except Exception as e: print("LINE 背景處理錯誤:", e)
    
@handler.add(JoinEvent)
def handle_join(event):
    """當機器人被邀請加入群組時觸發"""
    check_subscription(get_source_id(event))
    try: line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="小幫手報到！", contents=get_welcome_flex("https://erratic-penguin-857.notion.site/M-3069463a3aa78018be13fe885278b1cc")))
    except Exception as e: print(f"Join Error: {e}")

@handler.add(MemberJoinedEvent)
def handle_member_joined(event):
    """當新成員加入群組時觸發"""
    if event.source.type in ("group", "room"):
        line_bot_api.reply_message(event.reply_token, build_join_roster_guide_flex())

def sanitize_register_line(line: str) -> str:
    """清理備份/多行貼上的單行內容，無效行回傳空字串"""
    line = line.strip() if line else ""
    # 集中所有過濾條件，startswith 可直接吃 Tuple
    if not line or line.startswith(("📦", "—")) or "王表備份" in line or "\n" in line: return ""
    # 兩次正則替換嵌套，最後再 strip
    return re.sub(r"\s{2,}", " ", re.sub(r"\s*#\s*過\s*\d+", "", line)).strip()

def build_kpi_backup_text(kpi_db):
    """建立 KPI 備份文字"""
    return "\n".join(["__KPI_START__"] + [f"{get_username(uid)} {uid} {count}" for uid, count in kpi_db.items()] + ["__KPI_END__"])

#-------------------------------------------------------------****訊息判斷****---------------------------------------
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    group_id = get_source_id(event) 
    text = event.message.text
    user = event.source.user_id
    user_id = event.source.user_id
    text = event.message.text.strip()
    msg = text
    raw_text = event.message.text.strip()
    lines = raw_text.splitlines()
    success_count = 0
    failed_lines = []
    # 在進入迴圈前，先定義好模式判斷
    is_multi_register = len(lines) > 1
    # 只有包含「📦」或「備份」字眼的多行訊息，才判定為靜音備份模式
    is_backup_mode = is_multi_register and ("📦" in raw_text or "備份" in raw_text)
    db = load_db()
    group_id = get_source_id(event)
    db.setdefault("boss", {})
    db["boss"].setdefault(group_id, {})
    raw_text = event.message.text.strip()
    msg_text_no_space = raw_text.replace(" ", "")
    text = event.message.text.strip()
    # 🌟 關鍵：確保 group_id 是透過轉換函式取得的共用 ID
    group_id = get_source_id(event) 
    user_id = event.source.user_id
    msg_text = event.message.text.strip()


    # 設定台灣時區
    TZ = pytz.timezone('Asia/Taipei')
    # ========================================================================
    # ⚔️ 固定王系統主邏輯 (攔截、紀錄、統計)
    # ========================================================================    # 【功能 A】攔截 iOS 捷徑的提醒，並啟動 5 分鐘倒數
    if "⏰固定王提醒" in text and "倒數5️⃣分鐘" in text and "Boss" in text:
        # 利用 next 與生成器直接從多行字串中抽出王名，找不到給預設
        boss_name = next((line.split("Boss")[1].strip() for line in text.split('\n') if "Boss" in line), "未知王")
        line_bot_api.reply_message(event.reply_token, build_confirmation_flex(boss_name))
        return threading.Timer(900.0, auto_mark_missed, args=[group_id, boss_name]).start()

    # 【功能 B】處理 Flex 卡片的按鈕回覆 (儲存至資料庫防重複)
    if text.startswith("紀錄 "):
        if len(parts := text.split(" ", 2)) < 3:
            return line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 紀錄格式錯誤，請點擊卡片上的按鈕進行回報。"))
        status, boss_name = parts[1], parts[2]
        
        try:
            if not (conn := get_pg_conn()): raise Exception("無法取得連線")
            with conn, conn.cursor() as cur:
                # 檢查 20 分鐘內的紀錄，若存在則直接回傳警告卡片並中斷
                cur.execute("SELECT status FROM fixed_boss_records2 WHERE group_id=%s AND boss_name=%s AND record_time >= (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Taipei' - INTERVAL '20 minutes')", (group_id, boss_name))
                if existing := cur.fetchone():
                    return line_bot_api.reply_message(event.reply_token, build_duplicate_warning_flex(boss_name, existing[0]))
                
                cur.execute("INSERT INTO fixed_boss_records2 (group_id, boss_name, status) VALUES (%s, %s, %s)", (group_id, boss_name, status))
            return line_bot_api.reply_message(event.reply_token, build_record_success_flex(boss_name, status))
        except Exception as e:
            print(f"❌ 寫入 fixed_boss_records2 失敗: {e}")
            return line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 紀錄寫入失敗，請檢查後台！錯誤訊息: {e}"))

    # 【功能 C】送出報表 + 12:00 靜默刪除
    if text == "固定王統計":
        try:
            if not (conn := get_pg_conn()): raise Exception("無法取得連線")
            with conn, conn.cursor() as cur:
                cur.execute("SELECT status, COUNT(*), ARRAY_AGG(TO_CHAR(record_time AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Taipei', 'MM/DD HH24:MI') || ' (' || boss_name || ')') FROM fixed_boss_records2 WHERE group_id = %s GROUP BY status", (group_id,))
                # 初始化預設字典，並透過迴圈直接更新
                stats_dict, details = {"我方擊殺": 0, "敵人吃": 0, "漏掉": 0}, {"敵人吃": [], "漏掉": []}
                for s_name, count, t_list in cur.fetchall():
                    stats_dict[s_name] = count
                    if s_name in details and t_list: details[s_name] = t_list
                
                line_bot_api.reply_message(event.reply_token, build_stats_report_flex(sum(stats_dict.values()), stats_dict, details))
                
                # 靜默刪除邏輯：判斷目前台灣時間是否為 12:00
                if datetime.now(TZ).strftime("%H:%M") == "12:00":
                    cur.execute("DELETE FROM fixed_boss_records2 WHERE group_id = %s", (group_id,))
                    print(f"🕛 台灣時間 12:00 靜默清空群組 {group_id} 的紀錄完成。")
        except Exception as e:
            print(f"❌ 統計失敗: {e}"); return safe_reply(event, "❌ 讀取統計資料失敗，請稍後再試。", None)
        return
    
    # ==========================================
    # 訂閱制權限攔截與狀態查詢
    # ==========================================
    is_allowed, expiry, status_text = check_subscription(group_id)
    
    # 1. 攔截未授權且非系統指令的操作
    if not is_allowed and raw_text not in ("ID", "狀態", "id"):
        return line_bot_api.reply_message(event.reply_token, build_subscription_flex(status_text, expiry.strftime('%Y-%m-%d %H:%M')))
        
    # 2. 處理「狀態」查詢指令
    if raw_text == "狀態":
        return line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text=f"權限狀態：{status_text}", contents=get_status_flex(status_text, expiry.strftime('%Y-%m-%d'), max(0, (expiry - now_tw()).days))))
    
    # ========================================================================
    # 🔄 交班系統主邏輯 (預約接班、當班切換)
    # ========================================================================
    if msg_text_no_space.replace("@All", "") in ("交班", "交接", "換人", "換手"):
        with get_pg_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT next_user_id FROM shift_info WHERE group_id = %s", (group_id,))
            new_current = row[0] if (row := cur.fetchone()) else None
            
            cur.execute("INSERT INTO shift_info (group_id, current_user_id, next_user_id) VALUES (%s, %s, NULL) ON CONFLICT (group_id) DO UPDATE SET current_user_id = EXCLUDED.current_user_id, next_user_id = NULL, updated_at = NOW()", (group_id, new_current))
            conn.commit()
        return line_bot_api.reply_message(event.reply_token, build_shift_status_flex(group_id, new_current, None))

    elif raw_text == "接班":
        with get_pg_conn() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO shift_info (group_id, next_user_id) VALUES (%s, %s) ON CONFLICT (group_id) DO UPDATE SET next_user_id = EXCLUDED.next_user_id, updated_at = NOW()", (group_id, user_id))
            conn.commit()
        return line_bot_api.reply_message(event.reply_token, build_shift_success_flex(get_username(user_id)))
            
    # ========================================================================
    # ⏭️ 輪空登記 (跳過本次重生)
    # ========================================================================
    # 判斷是否為輪空指令
    if len(parts) >= 2 and ("空" in parts[1] or "輪空" in parts[1]):
        boss_input = parts[0]
        note = parts[1]
        
        # 轉換王名別名
        boss_name = None
        for real_name, aliases in alias_map.items():
            if boss_input == real_name or boss_input in aliases:
                boss_name = real_name
                break
        
        # 若找到王，執行輪空登記
        if boss_name:
            handle_boss_skipped(event, group_id, boss_name, user_id, note)
        
        return # 只要符合 "空" 的格式，不管有沒有找到王都中斷後續判定
    
    # ========================================================================
    # 🗑️ 刪除單一王紀錄
    # ========================================================================
    if event.message.text.strip().startswith("刪 "):
        name_input = event.message.text.strip()[2:].strip()
        if name_input:
            success, final_name = delete_boss_records_by_alias(get_source_id(event), name_input)
            alt_text = f"🗑 清除成功：{final_name}" if success else "❌ 清除失敗"
            line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text=alt_text, contents=get_delete_result_flex(success, name_input, final_name)))
            return
    
    # ========================================================================
    # 💰 競標系統 (發起、下標、結標)
    # ========================================================================
    if raw_text.startswith("掉落 "):
        active_auctions[group_id] = {"item": (item := raw_text[3:].strip()), "bid": 0, "bidder_name": None, "bidder_id": None}
        return line_bot_api.reply_message(event.reply_token, build_auction_flex(item, 0, None))

    elif raw_text.startswith("下標 ") and group_id in active_auctions:
        try:
            new_bid, current = int(raw_text[3:].strip()), active_auctions[group_id]
            if new_bid > current["bid"]:
                active_auctions[group_id].update({"bid": new_bid, "bidder_name": (u_name := get_username(user_id)), "bidder_id": user_id})
                return line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text=f"🔨 出價更新：{new_bid} 鑽", contents=FlexContainer.new_from_json_dict(build_auction_flex(current["item"], new_bid, u_name))))
            else:
                # 單行化失敗卡片 JSON
                err_flex = {"type":"bubble","size":"mega","body":{"type":"box","layout":"vertical","spacing":"md","contents":[{"type":"text","text":"❌ 出價無效","weight":"bold","color":"#E74C3C","size":"md"},{"type":"text","text":"出價需高於目前的最高標。","size":"sm","color":"#666666"},{"type":"box","layout":"vertical","margin":"md","backgroundColor":"#FEF5E7","paddingAll":"md","cornerRadius":"sm","contents":[{"type":"text","text":f"目前最高：{current['bid']} 💎","size":"sm","color":"#D68910","weight":"bold","align":"center"}]},{"type":"text","text":f"💡 建議出價：{current['bid'] + 1} 鑽以上","size":"xs","color":"#aaaaaa","align":"center"}]}}
                return line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="❌ 出價無效", contents=err_flex))
        except ValueError:
            return  # 輸入非數字則靜默跳出

    elif raw_text == "結標" and group_id in active_auctions:
        res = active_auctions.pop(group_id)
        msg = f"🎊 競標結束！\n\n📦 物品：{res['item']}\n👤 得標者：{res['bidder_name']}\n💰 金額：{res['bid']} 鑽\n\n恭喜得標！請雙方進行交易。" if res["bidder_name"] else f"已取消【{res['item']}】的競標（無人下標）。"
        return line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))

    # ========================================================================
    # 📝 名冊系統 (加入、更新確認)
    # ========================================================================
    db.setdefault("__ROSTER_WAIT__", {})
    if raw_text.startswith("加入名冊"):
        if len(parts := raw_text.split(" ", 2)) < 3:
            return line_bot_api.reply_message(event.reply_token, TextSendMessage("❌ 用法：加入名冊 血盟名 遊戲名"))
        _, clan, game_name = parts
        
        # 已存在 → 進入等待更新狀態並發送確認卡片
        if exists := roster_get_by_user(user_id):
            db["__ROSTER_WAIT__"][user_id] = {"action": "update", "clan": clan, "name": game_name}
            save_db(db)
            return line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="名冊已存在", contents=build_roster_confirm_update_flex(exists[0], exists[1], game_name, clan)))
            
        # 不存在 → 直接新增
        roster_insert(user_id, game_name, clan, get_line_display_name(user_id))
        return line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="已加入名冊", contents=build_roster_added_flex(clan, game_name)))

    elif raw_text == "確認修改":
        if not (session := db.get("__ROSTER_WAIT__", {}).pop(user_id, None)) or session.get("action") != "update": return
        
        roster_update(user_id, (new_name := session["name"]), (new_clan := session["clan"]))
        save_db(db)
        
        # 單行化成功卡片 JSON
        success_flex = {"type":"bubble","size":"mega","header":{"type":"box","layout":"vertical","contents":[{"type":"text","text":"✅ 更新成功","weight":"bold","color":"#1DB446","size":"lg"}],"paddingBottom":"none"},"body":{"type":"box","layout":"vertical","spacing":"md","contents":[{"type":"text","text":"您的名冊資訊已同步更新完成。","size":"sm","color":"#8c8c8c"},{"type":"separator","margin":"lg"},{"type":"box","layout":"vertical","margin":"lg","spacing":"sm","contents":[{"type":"box","layout":"horizontal","contents":[{"type":"text","text":"🛡️ 血盟","size":"sm","color":"#aaaaaa","flex":2},{"type":"text","text":f"{new_clan}","size":"sm","color":"#111111","flex":4,"align":"end","weight":"bold"}]},{"type":"box","layout":"horizontal","contents":[{"type":"text","text":"👤 名字","size":"sm","color":"#aaaaaa","flex":2},{"type":"text","text":f"{new_name}","size":"sm","color":"#111111","flex":4,"align":"end","weight":"bold"}]}]}]}}
        return line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="✅ 名冊更新成功", contents=success_flex))
    
    # ========================================================================
    # 🔍 查詢個人名冊
    # ========================================================================
    if raw_text == "查自己":
        if not (profile := get_roster_profile(user_id)):
            # 單行化查無資料卡片 JSON
            no_flex = {"type":"bubble","size":"mega","header":{"type":"box","layout":"vertical","contents":[{"type":"text","text":"查無個人名冊","weight":"bold","color":"#E74C3C","size":"lg"}],"paddingBottom":"none"},"body":{"type":"box","layout":"vertical","spacing":"md","contents":[{"type":"text","text":"系統目前找不到您的登記資訊。請先完成加入名冊！","wrap":True,"size":"sm","color":"#666666"},{"type":"box","layout":"vertical","margin":"lg","backgroundColor":"#F8F9FA","paddingAll":"md","cornerRadius":"sm","contents":[{"type":"text","text":"💡 加入指令：","size":"xs","color":"#8c8c8c","weight":"bold"},{"type":"text","text":"加入名冊 [血盟] [遊戲名字]","size":"sm","color":"#34495E","margin":"sm"}]}]}}
            return line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="❌ 尚未加入名冊", contents=no_flex))
            
        return line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="我的名冊資料", contents=build_roster_self_flex(profile["name"], profile["clan"])))
    
    # ========================================================================
    # 🗑️ 名冊刪除與取消操作
    # ========================================================================
    if raw_text == "刪除名冊":
        if not (profile := get_roster_profile(user_id)):
            # 單行化查無資料卡片 JSON (與查自己共用)
            no_flex = {"type":"bubble","size":"mega","header":{"type":"box","layout":"vertical","contents":[{"type":"text","text":"查無個人名冊","weight":"bold","color":"#E74C3C","size":"lg"}],"paddingBottom":"none"},"body":{"type":"box","layout":"vertical","spacing":"md","contents":[{"type":"text","text":"系統目前找不到您的登記資訊。請先完成加入名冊！","wrap":True,"size":"sm","color":"#666666"},{"type":"box","layout":"vertical","margin":"lg","backgroundColor":"#F8F9FA","paddingAll":"md","cornerRadius":"sm","contents":[{"type":"text","text":"💡 加入指令：","size":"xs","color":"#8c8c8c","weight":"bold"},{"type":"text","text":"加入名冊 [血盟] [遊戲名字]","size":"sm","color":"#34495E","margin":"sm"}]}]}}
            return line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="❌ 尚未加入名冊", contents=no_flex))
            
        return line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="確認刪除名冊", contents=build_roster_delete_confirm_flex(profile["name"])))

    elif raw_text == "確認刪除":
        roster_delete(user_id)
        return line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="名冊已刪除", contents=build_roster_deleted_flex()))

    elif raw_text == "取消" and db.get("__ROSTER_WAIT__", {}).pop(user_id, None):
        save_db(db)
        return line_bot_api.reply_message(event.reply_token, TextSendMessage("❎ 已取消操作"))
    
    # ========================================================================
    # 🔍 查詢名冊 (未完成~支援關鍵字與 @ 標籤過濾)
    # ========================================================================
    if raw_text.startswith("查名冊"):
        if len(parts := raw_text.split(maxsplit=1)) < 2:
            return line_bot_api.reply_message(event.reply_token, TextSendMessage("用法：查名冊 關鍵字\n例如：查名冊 威士忌"))
        
        with get_pg_conn() as conn, conn.cursor() as cur:
            # 實作註解需求：利用 replace 拔除 @ 符號，確保 Mention 也能正確搜尋
            cur.execute("SELECT game_name, line_name, clan_name FROM roster WHERE game_name ILIKE %s ORDER BY game_name LIMIT 10", (f"%{parts[1].replace('@', '').strip()}%",))
            
            # 透過海象運算子直接取值，並用三元表達式決定回傳的訊息類型
            reply = FlexSendMessage(alt_text="名冊查詢結果", contents=build_roster_flex(rows)) if (rows := cur.fetchall()) else TextSendMessage("❌ 查無符合的名冊資料")
            
        return line_bot_api.reply_message(event.reply_token, reply)
    
    # ========================================================================
    # 📜 基礎資訊查詢 (王別名列表、重生時間表)
    # ========================================================================
    if raw_text == "王列表":
        return line_bot_api.reply_message(event.reply_token, TextSendMessage(build_boss_list_text()))

    elif raw_text == "王重生":
        return line_bot_api.reply_message(event.reply_token, TextSendMessage(build_boss_cd_list_text()))
    
    # ========================================================================
    # 📋 查詢血盟名冊 (支援全部或特定血盟)
    # ========================================================================
    if raw_text.startswith("名冊"):
        keyword = parts[1] if len(parts := raw_text.split(maxsplit=1)) > 1 else "全部"
        rows = query_roster(keyword) if keyword != "全部" else query_roster()
        
        return line_bot_api.reply_message(event.reply_token, build_roster_search_flex(keyword, [(g, c, "") for g, c in rows]))
    
    # ========================================================================
    # 🔌 開機時間初始化 (批次設定 CD 王重生時間)
    # ========================================================================
    if raw_text.startswith("開機 "):
        if not (base_time := parse_time(raw_text[3:].strip())):
            return line_bot_api.reply_message(event.reply_token, TextSendMessage("❌ 時間格式錯誤，請使用 HHMM 或 HHMMSS"))
        
        init_cd_boss_with_given_time(group_id, base_time, user_id, cd_map)
        time_str = base_time.strftime('%H:%M')
        return line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text=f"🔌 開機時間已紀錄：{time_str}", contents=build_boot_init_flex(time_str)))

    # ========================================================================
    # 🧹 資料清除與 KPI 結算
    # ========================================================================
    if raw_text == "clear":
        db.setdefault("__WAIT__", {})[group_id] = {"user": user_id}
        save_db(db)
        return line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="清除確認", contents=clear_confirm_flex()))

    elif raw_text == "確定清除":
        try:
            # 權限檢查：利用連續 get() 安全取值，若不是發起人則提早退出
            if db.get("__WAIT__", {}).get(group_id, {}).get("user") != user_id: return

            start, end = get_kpi_range(now_tw())
            # 內聯呼叫：將撈取資料直接餵給計算 KPI 的函式
            kpi_data = calculate_kpi(get_all_records_for_kpi(group_id, start, end), start, end)
            
            # 實體刪除與字典清理
            delete_all_boss_records(group_id)
            db.get("boss_db", {}).pop(group_id, None)
            db.get("__WAIT__", {}).pop(group_id, None)
            save_db(db)

            if kpi_data:
                # 列表推導式直接產生 Flex 支援的排序清單
                display = [(get_username(uid), count) for uid, count in sorted(kpi_data.items(), key=lambda x: x[1], reverse=True)]
                bubble = build_kpi_flex("📊 本週 KPI 結算", f"{start.strftime('%m/%d %H:%M')} ～ {end.strftime('%m/%d %H:%M')}", display)
                return line_bot_api.reply_message(event.reply_token, [FlexSendMessage(alt_text="KPI 結算", contents=bubble), TextSendMessage("🗑️ 資料已完全清空，KPI 結算完畢。")])
            
            return line_bot_api.reply_message(event.reply_token, TextSendMessage("🗑️ 資料已清空 (本週無符合條件之 KPI 紀錄)。"))
        
        except Exception as e:
            print(traceback.format_exc()); return line_bot_api.reply_message(event.reply_token, TextSendMessage(f"⚠️ 清除失敗：{e}"))

    elif raw_text == "取消清除":
        db.get("__WAIT__", {}).pop(group_id, None); save_db(db)
        return line_bot_api.reply_message(event.reply_token, TextSendMessage("❎ 已取消清除"))
    
    # ========================================================================
    # 🔍 查詢單一王歷史紀錄
    # ========================================================================
    if raw_text.startswith("查 "):
        try:
            real_name = get_real_boss_name(raw_text[2:].strip())
            history = get_boss_history(group_id, real_name)
            print(f"DEBUG: 轉換 '{raw_text[2:].strip()}' -> '{real_name}', 找到 {len(history)} 筆") # 壓縮 DEBUG 輸出
            
            # 使用三元表達式一行決定要回傳 Flex 還是純文字錯誤
            reply = FlexSendMessage(alt_text=f"{real_name} 紀錄", contents=build_boss_history_flex(real_name, history)) if history else TextSendMessage(text=f"找不到「{real_name}」的紀錄，請確認名稱是否正確。")
            return line_bot_api.reply_message(event.reply_token, reply)
        except Exception as e:
            print(f"查詢錯誤: {e}"); return line_bot_api.reply_message(event.reply_token, TextSendMessage(text="查詢過程發生錯誤，請稍後再試。"))

    # ========================================================================
    # ↩️ 撤銷最後一筆紀錄
    # ========================================================================
    if raw_text.startswith("撤"):
        if not (boss_input := raw_text[1:].strip()):
            return line_bot_api.reply_message(event.reply_token, TextSendMessage("請輸入要撤銷的王名，例如：撤 克特"))
            
        success, result_obj = undo_last_boss_record(group_id, boss_input)
        if result_obj:
            return line_bot_api.reply_message(event.reply_token, result_obj)
        print(f"❌ 撤銷失敗：找不到 {boss_input} 的紀錄或資料庫回傳異常")

    # ========================================================================
    # 📊 KPI 統計報表查詢
    # ========================================================================
    if raw_text.upper() == "KPI":
        period_text, ranking = get_kpi_ranking(group_id)
        if not ranking:
            # 單行化無資料卡片 JSON
            no_flex = {"type":"bubble","size":"mega","body":{"type":"box","layout":"vertical","spacing":"md","contents":[{"type":"text","text":"📊 KPI 統計報表","weight":"bold","color":"#111111","size":"md"},{"type":"separator","margin":"md"},{"type":"box","layout":"vertical","margin":"lg","spacing":"sm","alignItems":"center","contents":[{"type":"text","text":"∅","size":"xxl","color":"#cccccc","weight":"bold"},{"type":"text","text":"目前尚無相關紀錄","size":"sm","color":"#aaaaaa"}]},{"type":"box","layout":"horizontal","margin":"md","contents":[{"type":"text","text":"查詢區間","size":"xs","color":"#bbbbbb","flex":0},{"type":"text","text":f"{period_text}","size":"xs","color":"#bbbbbb","align":"end"}]}]}}
            return line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text=f"📊 {period_text} 尚無 KPI 紀錄", contents=no_flex))

        # 取得完整時間區間並回覆榜單
        start, end = get_kpi_range(now_tw())
        bubble = build_kpi_flex("本週 KPI 排行榜", f"{start.strftime('%m/%d %H:%M')} ～ {end.strftime('%m/%d %H:%M')}", ranking)
        return line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="本週 KPI 排行榜", contents=bubble))
    
    # ========================================================================
    # 📢 重生時間列表 (含防連結處理)
    # ========================================================================
    if raw_text.lower() in ("出", "出出", "tj", "tjtj"):
        is_force = raw_text.lower() in ("出出", "tjtj")
        now, time_items, unregistered = now_tw(), [], []
        boss_db = get_latest_boss_records(group_id)

        for boss, cd in cd_map.items():
            if not boss_db.get(boss): unregistered.append(boss); continue
            
            rec = boss_db[boss][-1]
            base_t = datetime.fromisoformat(rec["respawn"]).astimezone(TZ)
            rounds = int((now - base_t).total_seconds() // (cd * 3600))
            
            # 計算重生時間與漏打次數
            disp_t = base_t + (rounds if now < base_t + rounds * timedelta(hours=cd) else rounds + 1) * timedelta(hours=cd)
            missed = max(0, rounds + (1 if now >= base_t + rounds * timedelta(hours=cd) else 0))
            passed = int((now - (disp_t - timedelta(hours=cd))).total_seconds() // 60) if missed == 0 else None
            
            # 組裝字串 (利用 \u200b 防連結)
            line = f"{disp_t.strftime('%H:%M:%S').replace(':', ':' + chr(0x200b))} {boss}"
            if rec.get("note"): line += f"（{rec['note'].strip()}）"
            if passed is not None and passed <= 30: line += f" <{passed}分未打>"
            if missed > 0: line += f" #過{missed}"
            time_items.append((disp_t, line))

        time_items.sort(key=lambda x: x[0])
        # 決定顯示區間
        display = time_items[:14] if is_peak_time() and not is_force else time_items
        output = [f"📢【即將重生列表{'｜熱門' if is_peak_time() and not is_force else ('｜完整' if is_force else '')}】", ""]
        output += [line for _, line in display]
        if is_peak_time() and not is_force: output += ["", "👉 輸入「出出」可查看完整列表"]
        if unregistered: output += ["", "— 未登記 —"] + unregistered

        return line_bot_api.reply_message(event.reply_token, TextSendMessage("\n".join(output)))

    # ========================================================================
    # ⚔️ 帶擊殺按鈕的王列表 (快速擊殺回報)
    # ========================================================================
    if raw_text == "打王":
        now, time_items = now_tw(), []
        boss_db = get_latest_boss_records(group_id)

        for boss, cd in cd_map.items():
            if not boss_db.get(boss): continue
            
            rec = boss_db[boss][-1]
            base_t = datetime.fromisoformat(rec["respawn"]).astimezone(TZ)
            rounds = int((now - base_t).total_seconds() // (cd * 3600))
            
            # 計算時間
            disp_t = base_t + (rounds if now < base_t + rounds * timedelta(hours=cd) else rounds + 1) * timedelta(hours=cd)
            missed = max(0, rounds + (1 if now >= base_t + rounds * timedelta(hours=cd) else 0))
            passed = int((now - (disp_t - timedelta(hours=cd))).total_seconds() // 60) if missed == 0 else None
            
            # 組裝字串
            line = f"{disp_t.strftime('%H:%M:%S')} {boss}"
            if rec.get("note"): line += f"（{rec['note'].strip()}）"
            if passed is not None and passed <= 30: line += f" <{passed}分未打>"
            if missed > 0: line += f" #過{missed}"
            time_items.append((disp_t, line))

        time_items.sort(key=lambda x: x[0])
        return line_bot_api.reply_message(event.reply_token, build_kill_list_flex(f"⚔️ 快速擊殺列表 (近 {len(time_items[:15])} 隻)", time_items[:15]))


    # ========================================================================
    # 🆔 查詢群組 ID
    # ========================================================================
    if raw_text == "群組代碼":
        return line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"本群組的聊天室代碼為：\n{group_id}"))
    
    # ========================================================================
    # 📝 登記王 (解析、PG寫入、KPI同步)
    # ========================================================================
    restored_kpi, skip_kpi = {}, False
    for raw_line in filter(None, (l.strip() for l in lines)):
        if raw_line == "__KPI_START__": skip_kpi = True; continue
        if raw_line == "__KPI_END__":
            if restored_kpi: db.setdefault("kpi_backup", {})[now_tw().strftime("%Y-%m-%d")] = restored_kpi; save_db(db)
            skip_kpi = False; continue
        if skip_kpi: continue

        if not (clean := sanitize_register_line(raw_line)) or len(parts := clean.split()) < 2: failed_lines.append(raw_line); continue

        time_token, boss_name = parts[0], parts[1]
        note = " ".join(parts[2:]) if len(parts) > 2 else ""
        
        t = now_tw() if time_token in ["6", "6666", "K"] else parse_time(time_token)
        boss = get_boss(boss_name)
        cd = cd_map.get(boss) if boss else None
        
        if not t or not boss or cd is None: failed_lines.append(raw_line); continue

        # 寫入 PG
        save_boss_to_pg(group_id=group_id, boss_name=boss, kill_time=t, respawn_time=(respawn := t + timedelta(hours=cd)), user_id=user_id, note=note, source="backup" if is_backup_mode else "manual")
        success_count += 1

        # 非備份模式下的即時回覆
        if not is_backup_mode:
            registrar, k_str, r_str = get_username(user_id), t.strftime("%H:%M:%S"), respawn.strftime('%H:%M:%S')
            is_b = (getattr(event.source, 'group_id', None) == "Cfea8c07f23c410a1e328871f8573f5e5")
            safe_reply(event, build_register_boss_text(boss, k_str, r_str, registrar, note), build_register_boss_flex(boss, k_str, r_str, registrar, note, False, is_b))

    if is_backup_mode:
        summary = f"📦 備份登記完成：成功 {success_count} 隻" + (f"\n⚠️ 失敗 {len(failed_lines)} 行" if failed_lines else "")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(summary))

@app.get("/")
def root():
    return {"status": "OK"}

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000))
    )

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL 未設定")
