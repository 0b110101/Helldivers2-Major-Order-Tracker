import os
import json
import requests
from datetime import datetime, timezone

try:
    from deep_translator import GoogleTranslator
    TRANSLATOR_AVAILABLE = True
except ImportError:
    TRANSLATOR_AVAILABLE = False

DATA_FILE = "data/helldivers_state.json"
API_URL = "https://api.helldivers2.dev/raw/api/v2/MajorOrders"
FALLBACK_API_URL = "https://helldivers-2.fly.dev/api/v1/major-orders"

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SUPER_EARTH_ICON = "https://static.wikia.nocookie.net/helldivers_gamepedia/images/c/c2/Super_Earth.png"

def load_state():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_state(state):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def to_chinese(text):
    if not text:
        return ""
    if any('\u4e00' <= char <= '\u9fff' for char in text):
        return text
    if TRANSLATOR_AVAILABLE:
        try:
            return GoogleTranslator(source='auto', target='zh-CN').translate(text)
        except Exception as e:
            print(f"[Warn] 翻译失败，保留原文: {e}")
    return text

def format_duration(seconds):
    if seconds <= 0:
        return "即将结束"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    mins = (seconds % 3600) // 60
    parts = []
    if days > 0:
        parts.append(f"{days}天")
    if hours > 0:
        parts.append(f"{hours}小时")
    if mins > 0 and days == 0:
        parts.append(f"{mins}分钟")
    return " ".join(parts) if parts else "少于1分钟"

def send_discord_embed(embed_payload):
    if not WEBHOOK_URL:
        print("[Error] DISCORD_WEBHOOK_URL 未配置")
        return
    data = {
        "username": "超级地球战略指挥部",
        "avatar_url": SUPER_EARTH_ICON,
        "embeds": [embed_payload]
    }
    resp = requests.post(WEBHOOK_URL, json=data)
    resp.raise_for_status()

def fetch_major_orders():
    headers = {
        "Accept-Language": "zh-Hans,zh-CN;q=0.9",
        "User-Agent": "Helldivers-CN-Bot/1.0"
    }
    try:
        res = requests.get(API_URL, headers=headers, timeout=12)
        if res.status_code == 200:
            return res.json()
    except Exception as e:
        print(f"[Warn] 主 API 失败: {e}，尝试备用线路...")

    res = requests.get(FALLBACK_API_URL, timeout=12)
    res.raise_for_status()
    return res.json()

def main():
    state = load_state()
    try:
        orders = fetch_major_orders()
    except Exception as e:
        print(f"[Error] 获取指令失败: {e}")
        # 如果 API 失败，也要保存空或现有状态，确保文件存在
        save_state(state)
        return

    current_order = orders[0] if orders else None
    last_order_id = state.get("last_order_id")
    last_order_title = state.get("last_order_title", "作战任务")

    # 1. 检测上一条指令是否已结束
    if last_order_id:
        if not current_order or current_order.get("id") != last_order_id:
            print(f"[Info] 指令 {last_order_id} 已结束")
            embed_finished = {
                "title": "🔴 【战略通报】重要指令已结束",
                "description": f"### 战事封盘：{last_order_title}\n\n全体绝地潜兵请注意，该阶段战区统筹已截止。",
                "color": 0xE74C3C,
                "fields": [
                    {
                        "name": "🎖️ 战况与结算",
                        "value": "战区战果正由超级地球最高参谋部核实，相应勋章将陆续派发至各驱逐舰终端。",
                        "inline": False
                    }
                ],
                "footer": {"text": "超级地球最高指挥部 · 统辖管理区"},
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            send_discord_embed(embed_finished)
            state["last_order_id"] = None

    # 2. 检测新指令
    if current_order:
        cid = current_order.get("id")
        if cid != state.get("last_order_id"):
            setting = current_order.get("setting", {})
            raw_title = setting.get("overrideTitle") or f"行动 #{cid}"
            raw_brief = setting.get("overrideBrief") or "前线暂无补充简报，遵从终端既定战术引导。"

            title = to_chinese(raw_title)
            brief = to_chinese(raw_brief)

            reward_data = setting.get("reward", {})
            amount = reward_data.get("amount", 0) if isinstance(reward_data, dict) else 0
            reward_desc = f"🎖️ **{amount}** 战争债券勋章" if amount else "管理式民主的无上荣光"

            expires_in = current_order.get("expiresIn", 0)
            time_str = format_duration(expires_in)

            embed_new = {
                "title": "🟢 【优先警报】接收到新的重要指令",
                "description": f"### 🛡️ {title}\n\n> {brief}",
                "color": 0xF1C40F,
                "fields": [
                    {"name": "🎯 作战目标", "value": "根据战术地图指引解放或防守指定星系。", "inline": False},
                    {"name": "🎁 作战津贴", "value": reward_desc, "inline": True},
                    {"name": "⏱️ 作战时限", "value": f"`{time_str}`", "inline": True}
                ],
                "footer": {"text": "传播管理式民主 · 战略前线实况"},
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            send_discord_embed(embed_new)

            state["last_order_id"] = cid
            state["last_order_title"] = title

    # 无论有无新指令，都保存状态，确保该文件一定会被创建
    save_state(state)

if __name__ == "__main__":
    main()
