import json
import os


CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
    "call_strategy": "random",  # random, high, low, least_used, most_used, oldest, newest
    "custom_api_key": "",  # 空字符串表示不使用自定义api_key
}

if os.path.exists(CONFIG_FILE):
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception:
        config = DEFAULT_CONFIG
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
else:
    config = DEFAULT_CONFIG
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

CALL_STRATEGY = config.get("call_strategy", DEFAULT_CONFIG["call_strategy"])
CUSTOM_API_KEY = config.get("custom_api_key", DEFAULT_CONFIG["custom_api_key"])


def save_config():
    global CALL_STRATEGY, CUSTOM_API_KEY
    config["call_strategy"] = CALL_STRATEGY
    config["custom_api_key"] = CUSTOM_API_KEY
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def update_call_strategy(new_strategy: str):
    global CALL_STRATEGY
    CALL_STRATEGY = new_strategy
    save_config()


def update_custom_api_key(new_key: str):
    global CUSTOM_API_KEY
    CUSTOM_API_KEY = new_key
    save_config()
