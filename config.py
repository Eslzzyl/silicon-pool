import json
import os


CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
    "call_strategy": "random",  # random, high, low, least_used, most_used, oldest, newest, round_robin
    "custom_api_key": "",  # 空字符串表示不使用自定义api_key
    "free_model_api_key": "",  # 空字符串表示不使用特殊token来调用免费模型的api_key
    "admin_username": "admin",  # 默认管理员用户名
    "admin_password": "admin",  # 默认管理员密码
    "rpm_limit": 0,  # 每分钟请求数限制，0表示不限制
    "tpm_limit": 0,  # 每分钟处理令牌数限制，0表示不限制
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
FREE_MODEL_API_KEY = config.get("free_model_api_key", DEFAULT_CONFIG["free_model_api_key"])
ADMIN_USERNAME = config.get("admin_username", DEFAULT_CONFIG["admin_username"])
ADMIN_PASSWORD = config.get("admin_password", DEFAULT_CONFIG["admin_password"])
RPM_LIMIT = config.get("rpm_limit", DEFAULT_CONFIG["rpm_limit"])
TPM_LIMIT = config.get("tpm_limit", DEFAULT_CONFIG["tpm_limit"])


def save_config():
    global CALL_STRATEGY, CUSTOM_API_KEY, FREE_MODEL_API_KEY, ADMIN_USERNAME, ADMIN_PASSWORD, RPM_LIMIT, TPM_LIMIT
    config["call_strategy"] = CALL_STRATEGY
    config["custom_api_key"] = CUSTOM_API_KEY
    config["free_model_api_key"] = FREE_MODEL_API_KEY
    config["admin_username"] = ADMIN_USERNAME
    config["admin_password"] = ADMIN_PASSWORD
    config["rpm_limit"] = RPM_LIMIT
    config["tpm_limit"] = TPM_LIMIT
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


def update_free_model_api_key(new_key: str):
    global FREE_MODEL_API_KEY
    FREE_MODEL_API_KEY = new_key
    save_config()


def update_admin_credentials(username: str, password: str):
    global ADMIN_USERNAME, ADMIN_PASSWORD
    ADMIN_USERNAME = username
    ADMIN_PASSWORD = password
    save_config()


def update_rpm_limit(rpm_limit: int):
    global RPM_LIMIT
    RPM_LIMIT = rpm_limit
    save_config()


def update_tpm_limit(tpm_limit: int):
    global TPM_LIMIT
    TPM_LIMIT = tpm_limit
    save_config()
