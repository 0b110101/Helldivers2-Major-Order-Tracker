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
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SUPER_EARTH_ICON = "https://static.wikia.nocookie.net/helldivers_gamepedia/images/c/c2/Super_Earth.png"

# 当前稳定活跃的社区接口及备用数据源
API_ENDPOINTS = [
    "https://api.helldivers2.dev/api/v1/major-orders",
    "https://api.helldivers2.dev/raw/api/v2/MajorOrders",
    "https://helldiversstats.com/api/v1/historical-major-orders?limit=1"
]

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
            print(f"[Warn] 翻译服务失败，保留原文: {e}")
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
    resp = requests.post(WEBHOOK_URL, json=data, timeout=15)
    resp.raise_for_status()

def fetch_major_orders():
    # 官方要求必须包含 X-Super-Client 和 X-Super-Contact 标识
    headers = {
        "Accept-Language": "zh-Hans,zh-CN;q=0.9",
        "User-Agent": "Helldivers-Discord-Tracker/2.0",
        "X-Super-Client": "HelldiversDiscordNotifier",
        "X-Super-Contact": "admin@helldivers-bot.internal"
    }

    last_err = None
    for url in API_ENDPOINTS:
        try:
            print(f"[Info] 正在请求 API: {url} ...")
            res = requests.get(url, headers=headers, timeout=12)
            print(f"[Info] API 状态码: {res.status_code}")
            
            if res.status_code == 200:
                data = res.json()
                # 兼容部分分页结构（如 helldiversstats）
                if isinstance(data, dict):
                    if "data" in data and isinstance(data["data"], list):
                        return data["data"]
                    return [data]
                return data
            else:
                print(f"[Warn] 端点 {url} 返回 HTTP {res.status_code}: {res.text[:120]}")
        except Exception as e:
            print(f"[Warn] 端点 {url} 异常: {e}")
            last_err = e
            continue

    raise RuntimeError(f"所有可用 API 端点均不可用，最后异常: {last_err}")

def main():
    state = load_state()
    try:
        orders = fetch_major_orders()
    except Exception as e:
        print(f"[Error] 获取指令失败: {e}")
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
        cid = current_order.get("id") or current_order.get("id32") or current_order.get("orderId32")
        if cid and cid != state.get("last_order_id"):
            setting = current_order.get("setting", {})
            raw_title = setting.get("overrideTitle") or current_order.get("title") or f"行动 #{cid}"
            raw_brief = setting.get("overrideBrief") or current_order.get("brief") or "前线暂无补充简报，遵从终端既定战术引导。"

            title = to_chinese(raw_title)
            brief = to_chinese(raw_brief)

            # 勋章奖励解析
            reward_data = setting.get("reward", {})
            amount = reward_data.get("amount", 0) if isinstance(reward_data, dict) else 0
            if not amount:
                amount = current_order.get("rewardMedals", 0)
            
            reward_desc = f"🎖️ **{amount}** 战争债券勋章" if amount else "管理式民主的无上荣光"

            # 倒计时
            expires_in = current_order.get("expiresIn") or current_order.get("expiresInLastSeen", 0)
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

    save_state(state)

if __name__ == "__main__":
    main()
