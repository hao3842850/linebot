from boss_config import cd_map

def build_all_boss_quick_flex():

    boss_names = sorted(list(cd_map.keys()))
    
    rows = []
    # 每 4 隻王一列，減少垂直高度，避免超過螢幕
    for i in range(0, len(boss_names), 4):
        chunk = boss_names[i:i+4]
        cols = []
        for name in chunk:
            cols.append({
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#4682B4",
                "cornerRadius": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": name,
                        "size": "xxs", # 使用極小字體確保 4 欄塞得下
                        "align": "center",
                        "color": "#ffffff",
                        "weight": "bold",
                        "gravity": "center"
                    }
                ],
                "paddingAll": "8px", # 確保數值帶 px
                "action": {
                    "type": "message",
                    "label": name,
                    "text": f"6666 {name}"
                }
            })
        
        # 補齊空格
        while len(cols) < 4:
            cols.append({"type": "spacer", "flex": 1})
            
        rows.append({
            "type": "box",
            "layout": "horizontal",
            "spacing": "xs",
            "contents": cols
        })

    # 封裝成 Bubble
    bubble_content = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#2c3e50",
            "contents": [
                {"type": "text", "text": "快速登記 (6666)", "weight": "bold", "color": "#ffffff", "size": "sm", "align": "center"}
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": rows
        }
    }
    
    # 務必檢查這裡的 FlexSendMessage 拼字與結構
    return FlexSendMessage(alt_text="快速登記選單", contents=bubble_content)
def notify_boss_team_with_flex(group_id, boss_name):
    conn = get_pg_conn()
    cur = conn.cursor()
    try:
        # 1. 抓取打王組成員
        cur.execute("SELECT user_id FROM boss_team WHERE group_id = %s", (group_id,))
        rows = cur.fetchall()
        
        base_msg = f"【{boss_name}】即將在 5 分鐘後重生！"
        full_text = f"⏰ 提醒：{base_msg}"
        mention_payload = None  # 用來存標記資料的變數

        # 2. 手動建構標記 (使用字典而非類別)
        if rows:
            user_ids = [r[0] for r in rows]
            text_prefix = "📢 打王組集合！ "
            mentionees = []
            
            # 手動計算每個人的標記位置
            for i, uid in enumerate(user_ids[:50]): # LINE 限制上限 50 人
                mentionees.append({
                    "index": len(text_prefix) + i,
                    "length": 1,
                    "userId": uid
                })
            
            # 組合最終文字：前綴 + 空格(標記位) + 訊息
            full_text = f"{text_prefix}{' ' * len(mentionees)}\n{base_msg}"
            # 這就是 LINE API 需要的標記字典格式
            mention_payload = {"mentionees": mentionees}

        # 3. 定義 bubble (卡片內容)
        bubble = {
            "type": "bubble",
            "size": "sm",
            "header": {
                "type": "box", "layout": "vertical", "backgroundColor": "#E74C3C",
                "contents": [{"type": "text", "text": "⚔️ 大王警告", "color": "#ffffff", "weight": "bold", "size": "sm", "align": "center"}]
            },
            "body": {
                "type": "box", "layout": "vertical", 
                "contents": [
                    {"type": "text", "text": f"{boss_name}", "weight": "bold", "size": "xl", "align": "center", "margin": "md"},
                    {"type": "text", "text": "準備重生", "size": "sm", "color": "#aaaaaa", "align": "center"}
                ]
            }
        }

        # 4. 發送訊息 (直接將字典丟入 mention 參數)
        messages = [
            TextSendMessage(text=full_text, mention=mention_payload),
            FlexSendMessage(alt_text=f"警報: {boss_name}", contents=bubble)
        ]
        
        line_bot_api.push_message(group_id, messages)
            
    except Exception as e:
        print(f"通知出錯: {e}")
    finally:
        cur.close()
        conn.close()
def build_register_boss_flex(boss, kill_time, respawn_time, registrar, note=None):
    map_list = BOSS_MAP.get(boss, [])
    map_text = "、".join(map_list) if map_list else "未知"

    contents = [
            # ===== 標題 (僅 BOSS 名稱變色) =====
            {
                "type": "text",
                "text": "🔥 已登記 ", # 這行現在當作外殼
                "weight": "bold",
                "size": "lg",
                "contents": [
                    {
                        "type": "span",
                        "text": "🔥 已登記 "
                    },
                    {
                        "type": "span",
                        "text": boss,
                        "color": "#FF6D18", # 只有 BOSS 名稱會變紅色
                        "weight": "bold"
                    }
                ]
            },
            {
                "type": "separator",
                "margin": "md"
            },

        # ===== 資訊列 =====
        {
            "type": "box",
            "layout": "baseline",
            "contents": [
                {
                    "type": "text",
                    "text": "🗺️ 地圖：",
                    "size": "sm",
                    "color": "#888888",
                    "flex": 2
                },
                {
                    "type": "text",
                    "text": map_text,
                    "wrap": True,
                    "flex": 6
                }
            ]
        },
        {
            "type": "box",
            "layout": "baseline",
            "contents": [
                {
                    "type": "text",
                    "text": "🕒 死亡：",
                    "size": "sm",
                    "color": "#888888",
                    "flex": 2
                },
                {
                    "type": "text",
                    "text": kill_time,
                    "wrap": True,
                    "flex": 6
                }
            ]
        },
        {
            "type": "box",
            "layout": "baseline",
            "contents": [
                {
                    "type": "text",
                    "text": "✨ 重生：",
                    "size": "sm",
                    "color": "#888888",
                    "flex": 2
                },
                {
                    "type": "text",
                    "text": respawn_time,
                    "wrap": True,
                    "flex": 6
                }
            ]
        },
    ]

    # ===== 備註（同層級，不凸顯）=====
    if note:
        contents.append({
            "type": "box",
            "layout": "baseline",
            "contents": [
                {
                    "type": "text",
                    "text": "📌 備註：",
                    "size": "sm",
                    "color": "#888888",
                    "flex": 2
                },
                {
                    "type": "text",
                    "text": note,
                    "wrap": True,
                    "flex": 6
                }
            ]
        })

    # ===== 登記者 =====
    contents.extend([
        {
            "type": "separator",
            "margin": "lg"
        },
        {
            "type": "text",
            "text": f"👤 登記者：{registrar}",
            "size": "xs",
            "color": "#999999",
            "wrap": True
        }
    ])

    return FlexSendMessage(
        alt_text=f"已登記 {boss}",
        contents={
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": contents
            }
        }
    )
def build_help_flex():
    bubbles = []
    # 1️⃣ 登記王
    bubbles.append({
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": "📌 登記BOSS",
                    "weight": "bold",
                    "size": "lg"
                },
                {
                    "type": "text",
                    "text": "指令格式：",
                    "weight": "bold"
                },
                {
                    "type": "text",
                    "text": "6666 四色\nK 四色\n0930 四色\n093045 四色 備註",
                    "wrap": True
                },
                {
                    "type": "text",
                    "text": "※ 6666 = 現在時間 and K = 現在時間",
                    "size": "sm",
                    "color": "#888888"
                }
            ]
        }
    })
    # 2️⃣ 查詢王
    bubbles.append({
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": "🔍 查詢歷史登記",
                    "weight": "bold",
                    "size": "lg"
                },
                {
                    "type": "text",
                    "text": "查 王名",
                    "wrap": True
                },
                {
                    "type": "text",
                    "text": "範例：\n查 四色",
                    "wrap": True
                }
            ]
        }
    })
    # 3️⃣ 出王清單
    bubbles.append({
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": "⏰ 出王清單",
                    "weight": "bold",
                    "size": "lg"
                },
                {
                    "type": "text",
                    "text": "出",
                    "wrap": True
                },
                {
                    "type": "text",
                    "text": "顯示即將重生的BOSS",
                    "size": "sm",
                    "color": "#888888"
                }
            ]
        }
    })
    # 4️⃣ clear 說明
    bubbles.append({
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": "⚠️ 清除紀錄",
                    "weight": "bold",
                    "size": "lg",
                    "color": "#D32F2F"
                },
                {
                    "type": "text",
                    "text": "clear",
                    "wrap": True
                },
                {
                    "type": "text",
                    "text": "※ 確定清除所有時間\n需按下『確定清除』",
                    "size": "sm",
                    "color": "#888888",
                    "wrap": True
                }
            ]
        }
    })
    # 5️⃣ 小技巧
    bubbles.append({
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": "📃 BOSS資料",
                    "weight": "bold",
                    "size": "lg"
                },
                {
                    "type": "text",
                    "text": "王列表➡️所有王的簡稱\n王重生➡️所有王的CD時間",
                    "wrap": True
                }
            ]
        }
    })
    # 六 
    bubbles.append({
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": "🔌開機時間",
                    "weight": "bold",
                    "size": "lg"
                },
                {
                    "type": "text",
                    "text": "開機 時間",
                    "wrap": True
                },
                {
                    "type": "text",
                    "text": "範例：\n開機 2100",
                    "wrap": True
                }
            ]
        }
    })
    return FlexSendMessage(
        alt_text="伊娃小幫手 使用說明",
        contents={
            "type": "carousel",
            "contents": bubbles
        }
    )
def build_join_roster_guide_flex():
    return FlexSendMessage(
        alt_text="歡迎加入群組，請加入名冊",
        contents={
            "type": "bubble",
            "size": "mega",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": [
                    # ===== 標題 =====
                    {
                        "type": "text",
                        "text": "👋 歡迎加入群組",
                        "weight": "bold",
                        "size": "xl",
                        "wrap": True
                    },
                    {
                        "type": "text",
                        "text": "為了正確統計王表與 KPI\n請先完成名冊登記",
                        "wrap": True,
                        "size": "sm",
                        "color": "#666666"
                    },

                    {
                        "type": "separator",
                        "margin": "lg"
                    },

                    # ===== 指令區 =====
                    {
                        "type": "text",
                        "text": "✍️ 加入名冊方式",
                        "weight": "bold",
                        "size": "md"
                    },

                    {
                        "type": "box",
                        "layout": "vertical",
                        "spacing": "xs",
                        "backgroundColor": "#F7F7F7",
                        "paddingAll": "md",
                        "cornerRadius": "md",
                        "contents": [
                            {
                                "type": "text",
                                "text": "加入名冊 血盟名 遊戲角色名",
                                "size": "sm",
                                "weight": "bold",
                                "wrap": True
                            },
                            {
                                "type": "text",
                                "text": "📘 範例：加入名冊 酒窖 威士忌乄",
                                "size": "sm",
                                "color": "#777777",
                                "wrap": True
                            }
                        ]
                    },

                    {
                        "type": "separator",
                        "margin": "lg"
                    },

                    # ===== 補充說明 =====
                    {
                        "type": "text",
                        "text": "📌 完成後即可使用王表、吃王登記等功能",
                        "size": "xs",
                        "color": "#999999",
                        "wrap": True
                    }
                ]
            }
        }
    )
def build_query_record_bubble(boss, rec):
    respawn = datetime.fromisoformat(rec["respawn"]).astimezone(TZ)
    registrar = get_username(rec.get("user"))
    
    # 標題與基礎樣式
    contents = [
        {
            "type": "text",
            "text": f"📋 歷史紀錄｜{boss}",
            "weight": "bold",
            "size": "lg",
            "color": "#111111"
        },
        {
            "type": "separator",
            "margin": "md",
            "color": "#EEEEEE"
        }
    ]

    # 定義內部資料行模板
    def create_info_row(label, value, value_color="#333333", is_bold=False):
        return {
            "type": "box",
            "layout": "horizontal",
            "contents": [
                {"type": "text", "text": label, "size": "sm", "color": "#888888", "flex": 3},
                {"type": "text", "text": value, "size": "sm", "color": value_color, "flex": 7, "weight": "bold" if is_bold else "regular", "align": "end"}
            ]
        }

    # 資料區塊
    info_box = {
        "type": "box",
        "layout": "vertical",
        "margin": "lg",
        "spacing": "sm",
        "contents": [
            create_info_row("📅 登記日期", rec['date']),
            create_info_row("🕒 死亡時間", rec['kill']),
            # 重生時間用藍色加粗，方便一眼識別
            create_info_row("✨ 重生時間", respawn.strftime('%H:%M:%S'), value_color="#1756B7", is_bold=True),
            create_info_row("👤 登記者", registrar)
        ]
    }
    
    contents.append(info_box)

    # 備註區塊
    if rec.get("note"):
        contents.append({
            "type": "box",
            "layout": "vertical",
            "margin": "md",
            "paddingAll": "sm",
            "backgroundColor": "#FDFDFD",
            "contents": [
                {
                    "type": "text",
                    "text": f"📌 {rec['note']}",
                    "size": "xs",
                    "color": "#999999",
                    "wrap": True,
                }
            ]
        })

    return {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": contents,
            "paddingAll": "lg"
        }
    }
def clear_confirm_flex():
    return {
      "type": "bubble",
      "size": "mega",
      "header": {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": "#D32F2F",
        "contents": [
          {
            "type": "text",
            "text": "⚠️ 危險操作確認",
            "color": "#FFFFFF",
            "weight": "bold",
            "size": "md",
            "align": "center"
          }
        ]
      },
      "body": {
        "type": "box",
        "layout": "vertical",
        "spacing": "md",
        "contents": [
          {
            "type": "text",
            "text": "清除所有王表紀錄？",
            "weight": "bold",
            "size": "md",
            "wrap": True,
            "align": "center"
          },
          {
            "type": "text",
            "text": "此動作將會抹除資料庫中所有現存紀錄，且「無法復原」。請再次確認您的操作。",
            "wrap": True,
            "size": "xs",
            "color": "#888888",
            "align": "center"
          }
        ]
      },
      "footer": {
        "type": "box",
        "layout": "vertical",
        "spacing": "sm",
        "contents": [
          {
            "type": "button",
            "style": "primary",
            "color": "#D32F2F",
            "height": "sm",
            "action": {
              "type": "message",
              "label": "確定清除",
              "text": "確定清除"
            }
          },
          {
            "type": "button",
            "style": "link",
            "color": "#444444",
            "height": "sm",
            "action": {
              "type": "message",
              "label": "取消",
              "text": "取消清除"
            }
          }
        ]
      },
      "styles": {
        "footer": {
          "separator": True
        }
      }
    }
def build_boot_init_flex(base_time_str):
    return {
        "type": "bubble",
        "size": "mega",
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "lg",
            "contents": [
                {
                    "type": "text",
                    "text": "🔌 開機時間已紀錄",
                    "weight": "bold",
                    "size": "lg",
                    "color": "#2E7D32"
                },
                {
                    "type": "separator",
                    "margin": "md",
                    "color": "#EEEEEE"
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "lg",
                    "backgroundColor": "#F1F8E9",
                    "paddingAll": "md",
                    "cornerRadius": "md",
                    "contents": [
                        {
                            "type": "text",
                            "text": "🕒 開機時間",
                            "size": "xs",
                            "color": "#689F38",
                            "weight": "bold"
                        },
                        {
                            "type": "text",
                            "text": base_time_str,
                            "size": "md",
                            "weight": "bold",
                            "color": "#333333",
                            "margin": "xs"
                        }
                    ]
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "margin": "md",
                    "contents": [
                        {
                            "type": "text",
                            "text": "ℹ️ 系統已自動補齊尚未登記的 CD 王",
                            "size": "xs",
                            "color": "#999999",
                            "wrap": True,
                            "flex": 1
                        }
                    ]
                }
            ]
        }
    }
def build_auction_flex(item_name, highest_bid, bidder_name):
    display_bidder = bidder_name if bidder_name else "目前尚無人出價"
    
    bubble = {
        "type": "bubble",
        "header": {
            "type": "box", "layout": "vertical", "backgroundColor": "#E67E22",
            "contents": [{"type": "text", "text": "⚔️ 盟內裝備快閃競標", "weight": "bold", "color": "#FFFFFF", "size": "sm"}]
        },
        "body": {
            "type": "box", "layout": "vertical", "spacing": "md",
            "contents": [
                {"type": "text", "text": f"📦 物品：{item_name}", "weight": "bold", "size": "lg"},
                {"type": "separator"},
                {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
                    {"type": "box", "layout": "horizontal", "contents": [
                        {"type": "text", "text": "最高標", "size": "sm", "color": "#aaaaaa", "flex": 2},
                        {"type": "text", "text": f"{highest_bid} 鑽", "size": "sm", "weight": "bold", "color": "#E67E22", "flex": 4}
                    ]},
                    {"type": "box", "layout": "horizontal", "contents": [
                        {"type": "text", "text": "領先者", "size": "sm", "color": "#aaaaaa", "flex": 2},
                        {"type": "text", "text": f"{display_bidder}", "size": "sm", "flex": 4}
                    ]}
                ]}
            ]
        },
        "footer": {
            "type": "box", "layout": "vertical",
            "contents": [
                {"type": "text", "text": "輸入「下標 金額」參與競標", "size": "xs", "color": "#aaaaaa", "align": "center"}
            ]
        }
    }
    return FlexSendMessage(alt_text=f"競標中: {item_name}", contents=bubble)
def build_kpi_flex(title, period_text, ranking):
    rows = []
    # 定義前三名的特殊顏色與圖標
    top_styles = {
        0: {"color": "#FFD700", "weight": "bold", "icon": "🥇"},  # 金
        1: {"color": "#C0C0C0", "weight": "bold", "icon": "🥈"},  # 銀
        2: {"color": "#CD7F32", "weight": "bold", "icon": "🥉"}   # 銅
    }

    for idx, (name, count) in enumerate(ranking):
        style = top_styles.get(idx, {"color": "#666666", "weight": "regular", "icon": f"{idx+1}"})
        
        # 每一行的內容
        row_content = {
            "type": "box",
            "layout": "horizontal",
            "paddingAll": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": style["icon"],
                    "size": "sm",
                    "flex": 1,
                    "align": "center",
                    "weight": style.get("weight")
                },
                {
                    "type": "text",
                    "text": name,
                    "size": "sm",
                    "flex": 4,
                    "weight": style.get("weight"),
                    "color": "#333333" if idx < 3 else "#666666"
                },
                {
                    "type": "text",
                    "text": f"{count} 次",
                    "size": "sm",
                    "align": "end",
                    "flex": 2,
                    "weight": "bold",
                    "color": style["color"] if idx < 3 else "#333333"
                }
            ]
        }
        
        # 前三名加入淡色背景強調
        if idx < 3:
            row_content["backgroundColor"] = "#F8F9FA"
            row_content["cornerRadius"] = "md"
            row_content["margin"] = "xs"

        rows.append(row_content)

    return {
        "type": "bubble",
        "size": "kilo",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#1A237E",
            "contents": [
                {
                    "type": "text",
                    "text": f"🏆 {title}",
                    "color": "#FFFFFF",
                    "weight": "bold",
                    "size": "md"
                }
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {
                    "type": "text",
                    "text": f"📅 統計區間：{period_text}",
                    "size": "xs",
                    "color": "#888888",
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "xs",
                    "contents": rows
                }
            ]
        }
    }
def build_roster_added_flex(clan, game_name):
    return {
        "type": "bubble",
        "size": "mega",  # 成功訊息不需要太大，輕量化更精緻
        "body": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#FFFFFF",
            "paddingAll": "lg",
            "contents": [
                # 頂部成功圖示與文字
                {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [
                        {
                            "type": "text",
                            "text": "✅",
                            "size": "lg",
                            "flex": 0
                        },
                        {
                            "type": "text",
                            "text": "登記成功",
                            "weight": "bold",
                            "size": "md",
                            "color": "#2E7D32",
                            "margin": "md",
                            "flex": 1
                        }
                    ]
                },
                # 分割線
                {
                    "type": "separator",
                    "margin": "lg",
                    "color": "#EEEEEE"
                },
                # 資料卡片區塊
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "lg",
                    "spacing": "sm",
                    "contents": [
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "遊戲角色", "size": "xs", "color": "#888888", "flex": 3},
                                {"type": "text", "text": game_name, "size": "sm", "color": "#333333", "weight": "bold", "flex": 7, "align": "end"}
                            ]
                        },
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "所屬血盟", "size": "xs", "color": "#888888", "flex": 3},
                                {"type": "text", "text": clan, "size": "sm", "color": "#333333", "weight": "bold", "flex": 7, "align": "end"}
                            ]
                        }
                    ]
                },
                # 底部小字提醒
                {
                    "type": "text",
                    "text": "您現在可以正常使用王表功能了",
                    "size": "xxs",
                    "color": "#AAAAAA",
                    "margin": "xl",
                    "align": "center"
                }
            ]
        },
        "styles": {
            "body": {
                "cornerRadius": "md"
            }
        }
    }
def build_roster_confirm_update_flex(old_name, old_clan, new_name, new_clan):
    return {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "⚠️ 名冊已存在", "weight": "bold"},
                {"type": "text", "text": f"目前：{old_name} / {old_clan}"},
                {"type": "text", "text": f"修改為：{new_name} / {new_clan}"},
                {
                    "type": "button",
                    "action": {"type": "message", "label": "確認修改", "text": "確認修改"}
                },
                {
                    "type": "button",
                    "action": {"type": "message", "label": "取消", "text": "取消"}
                }
            ]
        }
    }
def build_roster_self_flex(game_name, clan):
    return {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "👤 我的名冊", "weight": "bold"},
                {"type": "text", "text": f"🎮 {game_name}"},
                {"type": "text", "text": f"🏰 {clan}"}
            ]
        }
    }
def build_roster_delete_confirm_flex(game_name):
    return {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "⚠️ 確認刪除名冊", "weight": "bold"},
                {"type": "text", "text": f"角色：{game_name}"},
                {
                    "type": "button",
                    "action": {"type": "message", "label": "確認刪除", "text": "確認刪除"}
                },
                {
                    "type": "button",
                    "action": {"type": "message", "label": "取消", "text": "取消"}
                }
            ]
        }
    }
def build_roster_deleted_flex():
    return {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "🗑 名冊已刪除", "weight": "bold"}
            ]
        }
    }
def build_roster_search_flex(keyword, rows):
    contents = []
    if not rows:
        contents.append({
            "type": "text",
            "text": "查無符合的名冊資料",
            "size": "sm",
            "color": "#888888"
        })
    else:
        for game_name, clan_name, line_name in rows:
            contents.append({
                "type": "box",
                "layout": "vertical",
                "spacing": "xs",
                "margin": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": f"🎮 角色：{game_name}",
                        "size": "sm",
                        "weight": "bold"
                    },
                    {
                        "type": "text",
                        "text": f"🏰 血盟：{clan_name}",
                        "size": "sm",
                        "weight": "bold"
                    },
                    {
                        "type": "text",
                        "text": f"📱 LINE名稱：{line_name}",
                        "size": "sm",
                        "weight": "bold"
                    },
                ]
            })
    bubble = {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [{
                "type": "text",
                "text": f"🔍 名冊查詢：{keyword}",
                "weight": "bold",
                "size": "lg"
            }]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": contents
        }
    }
    return FlexSendMessage(
        alt_text=f"名冊查詢：{keyword}",
        contents=bubble
    )
def build_roster_flex(rows):
    body_contents = []

    # === 標題欄位列 ===
    body_contents.append({
        "type": "box",
        "layout": "horizontal",
        "paddingAll": "8px",
        "backgroundColor": "#333333",  # 深色背景讓標題更醒目
        "contents": [
            {"type": "text", "text": "角色", "flex": 3, "size": "xs", "color": "#FFFFFF", "weight": "bold"},
            {"type": "text", "text": "血盟", "flex": 2, "size": "xs", "color": "#FFFFFF", "weight": "bold", "align": "center"},
            {"type": "text", "text": "LINE", "flex": 2, "size": "xs", "color": "#FFFFFF", "weight": "bold", "align": "end"}
        ]
    })

    # === 資料列 (帶斑馬紋邏輯) ===
    for i, (game_name, line_name, clan_name) in enumerate(rows):
        # 奇數行使用淺灰色背景
        bg_color = "#F9F9F9" if i % 2 == 1 else "#FFFFFF"
        
        body_contents.append({
            "type": "box",
            "layout": "horizontal",
            "paddingAll": "10px",
            "backgroundColor": bg_color,
            "contents": [
                {
                    "type": "text",
                    "text": game_name,
                    "flex": 3,
                    "size": "sm",
                    "weight": "bold",
                    "wrap": True,
                    "color": "#111111"
                },
                {
                    "type": "text",
                    "text": clan_name if clan_name else "-",
                    "flex": 2,
                    "size": "xs",
                    "align": "center",
                    "color": "#666666",
                    "margin": "sm"
                },
                {
                    "type": "text",
                    "text": line_name if line_name else "-",
                    "flex": 2,
                    "size": "xs",
                    "align": "end",
                    "color": "#1E90FF"  # 維持你原本的藍色區分
                }
            ]
        })

    # === 底部提醒 ===
    body_contents.append({
        "type": "box",
        "layout": "vertical",
        "margin": "md",
        "contents": [
            {"type": "separator", "color": "#EEEEEE"},
            {
                "type": "text",
                "text": "💡 資料有誤請連繫 @H. 進行修正",
                "size": "xxs",
                "color": "#AAAAAA",
                "align": "center",
                "margin": "md"
            }
        ]
    })

    return {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#F4F4F4",
            "paddingAll": "12px",
            "contents": [
                {
                    "type": "text",
                    "text": "📖 名冊資料",
                    "weight": "bold",
                    "size": "md",
                    "color": "#444444"
                }
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "none",
            "paddingAll": "0px",  # 滿版表格感
            "contents": body_contents
        }
    }
