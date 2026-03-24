#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
AI助手主入口 - 子命令 CLI + 配置热重载
支持 model/hotkey/role/prompt/config 子命令管理，以及 run 启动主程序
"""

import argparse
import os
import sys
import logging
import re
from datetime import datetime

from . import __version__
from .config import get_config, reload_config, ConfigManager
from .model_factory import ModelFactory
from .hotkey_manager import HotkeyManager
from .utils import setup_logging

logger = logging.getLogger("multi_ai_assistant")

# ===== 全局状态（用于热重载） =====
_factory = None
_hotkey_mgr = None


def _do_reload():
    """热重载：重新读取配置 → 清空模型缓存 → 重新注册快捷键"""
    global _factory, _hotkey_mgr
    reload_config()
    if _factory:
        _factory.clear_cache()
    if _hotkey_mgr:
        _hotkey_mgr.register_all()
    logger.info("热重载完成")
    print("✓ 配置已重新加载")


# ===== litellm 模型数据库查询 =====

def _extract_date_from_model(model_id):
    """从模型名称中提取日期"""
    # 匹配格式：YYYY-MM-DD, YYYYMMDD, MM-DD 等
    patterns = [
        r'(\d{4}-\d{2}-\d{2})',  # 2025-03-11
        r'(\d{4}\d{2}\d{2})',    # 20250311
        r'-(\d{4}-\d{2})-',      # -2025-03-
        r'-(\d{2}-\d{2})-',      # -03-11-
    ]
    
    for pattern in patterns:
        match = re.search(pattern, model_id)
        if match:
            date_str = match.group(1)
            try:
                # 标准化日期格式
                if len(date_str) == 8:  # YYYYMMDD
                    date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
                elif len(date_str) == 7:  # YYYY-MM
                    date_str = f"{date_str}-01"
                elif len(date_str) == 5:  # MM-DD
                    date_str = f"2024-{date_str}"  # 假设是2024年
                
                return datetime.strptime(date_str, "%Y-%m-%d")
            except:
                continue
    return None

def _get_model_priority(model_id, info):
    """计算模型优先级（数字越小优先级越高）"""
    priority = 1000  # 默认优先级
    
    # 1. 最新日期优先（权重：-500）
    date = _extract_date_from_model(model_id)
    if date:
        # 距今天数越近，优先级越高
        days_ago = (datetime.now() - date).days
        priority -= max(0, 500 - days_ago)
    
    # 2. 版本号优先（权重：-100）
    model_lower = model_id.lower()
    if 'gpt-5' in model_lower:
        priority -= 100
    elif 'gpt-4' in model_lower:
        if '4.1' in model_lower or '4o' in model_lower:
            priority -= 80
        else:
            priority -= 60
    elif 'claude-3.5' in model_lower or 'claude-3-5' in model_lower:
        priority -= 70
    elif 'gemini-2' in model_lower:
        priority -= 90
    
    # 3. 特殊标识优先（权重：-50）
    special_keywords = ['latest', 'preview', 'turbo', 'pro', 'max']
    for keyword in special_keywords:
        if keyword in model_lower:
            priority -= 50
            break
    
    # 4. 模式优先（权重：-20）
    mode = info.get('mode', '')
    if mode == 'chat':
        priority -= 20
    elif mode == 'embedding':
        priority -= 10
    
    # 5. 功能支持优先（权重：-10）
    if info.get('supports_vision', False):
        priority -= 10
    if info.get('supports_function_calling', False):
        priority -= 5
    
    return priority

def _get_litellm_models():
    """获取 litellm 支持的模型数据库（优先缓存）"""
    import json
    from pathlib import Path
    
    # 尝试从缓存读取
    cache_dir = Path(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))) / "multi_ai_assistant"
    cache_file = cache_dir / "litellm_model_cost.json"
    
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.debug(f"读取缓存失败: {e}")
    
    # 回退到内置数据
    try:
        import litellm
        return litellm.model_cost
    except Exception:
        return {}


def _search_models(query="", provider="", mode="chat", limit=50):
    """搜索 litellm 支持的模型（智能排序：优先显示最新模型）

    Args:
        query: 模型名关键词 (模糊匹配)
        provider: 限定提供商
        mode: 模型类型过滤 (chat/embedding/image_generation 等)
        limit: 最大返回条数
    """
    db = _get_litellm_models()
    query_lower = query.lower()
    provider_lower = provider.lower()

    # 收集所有匹配的模型
    matched_models = []
    for model_id, info in db.items():
        if model_id == "sample_spec":
            continue
        m_provider = info.get("litellm_provider", "")
        m_mode = info.get("mode", "")

        # 过滤
        if provider_lower and provider_lower not in m_provider.lower():
            continue
        if mode and m_mode != mode:
            continue
        if query_lower and query_lower not in model_id.lower():
            continue

        # 计算优先级
        priority = _get_model_priority(model_id, info)
        
        matched_models.append({
            "model": model_id,
            "provider": m_provider,
            "mode": m_mode,
            "max_input": info.get("max_input_tokens", 0),
            "max_output": info.get("max_output_tokens", 0),
            "vision": info.get("supports_vision", False),
            "function_calling": info.get("supports_function_calling", False),
            "priority": priority,
        })

    # 按优先级排序（数字越小越靠前）
    matched_models.sort(key=lambda x: x["priority"])

    # 返回前 limit 个（移除 priority 字段，因为这是内部使用的）
    results = []
    for model_data in matched_models[:limit]:
        result = model_data.copy()
        del result["priority"]  # 移除内部字段
        results.append(result)

    return results


def _resolve_model_in_db(db: dict, model_id: str) -> dict:
    """在 litellm 数据库中查找模型，支持 provider/model 前缀格式

    litellm 数据库 key 可能不带前缀 (如 'claude-3-5-sonnet-20241022')，
    但用户传入的标识符可能带前缀 (如 'anthropic/claude-3-5-sonnet-20241022')。

    Returns:
        匹配到的模型 info dict，未找到返回 None
    """
    if model_id in db:
        return db[model_id]
    # 尝试去掉 provider/ 前缀后再查找
    if "/" in model_id:
        bare_name = model_id.split("/", 1)[1]
        if bare_name in db:
            return db[bare_name]
    return None


def _get_providers():
    """获取 litellm 支持的所有提供商及其模型数量"""
    db = _get_litellm_models()
    providers = {}
    for model_id, info in db.items():
        if model_id == "sample_spec":
            continue
        p = info.get("litellm_provider", "unknown")
        mode = info.get("mode", "")
        if p not in providers:
            providers[p] = {"total": 0, "chat": 0}
        providers[p]["total"] += 1
        if mode == "chat":
            providers[p]["chat"] += 1
    return providers


# ===== 渠道模型获取 =====

def _fetch_channel_models(config, channel_name):
    """从指定渠道获取可用模型列表

    Args:
        config: ConfigManager 实例
        channel_name: 渠道名称

    Returns:
        (models_list, error_msg) — models_list 为 [{"id": ...}, ...] 或 [], error_msg 为错误信息或 None
    """
    channels = config.get("channels", {})
    if channel_name not in channels:
        return [], f"渠道 '{channel_name}' 不存在"

    ch_conf = channels[channel_name]
    ch_type = ch_conf.get("type", "custom")
    provider = ch_conf.get("provider", "")

    # 标准渠道 — 从 litellm 数据库获取
    if ch_type == "standard" and provider != "custom":
        db = _get_litellm_models()
        models = []
        for model_id, info in db.items():
            if model_id == "sample_spec":
                continue
            m_provider = info.get("litellm_provider", "")
            m_mode = info.get("mode", "")
            if m_provider == provider and m_mode == "chat":
                models.append({"id": model_id})
        return models, None

    # 自定义渠道 — 调用 /models API
    api_base = ch_conf.get("api_base") or os.getenv(ch_conf.get("api_base_env", ""), "")
    api_key = ch_conf.get("api_key") or os.getenv(ch_conf.get("api_key_env", ""), "")

    if not api_base:
        env_name = ch_conf.get("api_base_env", "")
        return [], f"API Base 未设置 (环境变量 {env_name} 为空)"
    if not api_key:
        env_name = ch_conf.get("api_key_env", "")
        return [], f"API Key 未设置 (环境变量 {env_name} 为空)"

    try:
        import requests
        headers = {"Authorization": f"Bearer {api_key}"}
        resp = requests.get(f"{api_base.rstrip('/')}/models", headers=headers, timeout=15)
        if resp.status_code != 200:
            return [], f"API 返回 HTTP {resp.status_code}"
        models_data = resp.json()
        models = [{"id": m.get("id")} for m in models_data.get("data", []) if m.get("id")]
        return models, None
    except Exception as e:
        return [], str(e)


def _sync_channel_models_to_config(config, channel_name, models_list):
    """将获取到的模型列表同步到 config.models 中

    注意: 模型 key 可能包含 '.' 和 '/'，不能使用 config.set() 的点分路径，
    需要直接操作 user_config 字典。

    Args:
        config: ConfigManager 实例
        channel_name: 渠道名称
        models_list: [{"id": "model-name"}, ...]

    Returns:
        (added_count, total_count)
    """
    channels = config.get("channels", {})
    ch_conf = channels.get(channel_name, {})
    ch_type = ch_conf.get("type", "custom")
    provider = ch_conf.get("provider", "")

    # 直接操作 user_config 字典，避免点分路径解析问题
    if "models" not in config.user_config:
        config.user_config["models"] = {}
    user_models = config.user_config["models"]

    existing_models = config.models or {}
    added = 0

    for m in models_list:
        model_id = m["id"]
        # 模型 key: channel_name/model_id
        model_key = f"{channel_name}/{model_id}"

        if model_key in existing_models:
            continue  # 已存在，跳过

        model_conf = {
            "model": model_id,
            "provider": channel_name,
            "type": "litellm",
        }

        # 标准渠道直接继承凭证环境变量
        if ch_type == "standard" and provider != "custom":
            api_key_env = ch_conf.get("api_key_env", "")
            if api_key_env:
                model_conf["api_key_env"] = api_key_env

        user_models[model_key] = model_conf
        added += 1

    config._rebuild()
    return added, len(models_list)


def _remove_channel_hotkeys(config, channel_name):
    """删除与指定渠道相关的快捷键
    
    Args:
        config: ConfigManager 实例
        channel_name: 渠道名称
        
    Returns:
        list: 被删除的快捷键列表
    """
    user_hotkeys = config.user_config.get("hotkeys", {})
    removed_hotkeys = []
    
    for key, binding in list(user_hotkeys.items()):
        if binding.get("action") == "chat":
            target = binding.get("target", "")
            # 检查目标模型是否属于被删除的渠道
            if target.startswith(f"{channel_name}/"):
                del user_hotkeys[key]
                removed_hotkeys.append(key)
    
    return removed_hotkeys


def _update_litellm_db():
    """从 GitHub 下载最新的 litellm 模型数据库

    Returns:
        (success, message)
    """
    import json
    from pathlib import Path

    url = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
    cache_dir = Path(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))) / "multi_ai_assistant"
    cache_file = cache_dir / "litellm_model_cost.json"

    try:
        import urllib.request
        print(f"正在从 GitHub 下载最新模型数据库...")
        req = urllib.request.Request(url, headers={"User-Agent": "multi_ai_assistant"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()

        new_db = json.loads(data)
        model_count = len([k for k in new_db if k != "sample_spec"])

        # 保存到缓存
        cache_dir.mkdir(parents=True, exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(new_db, f)

        # 更新当前运行时
        try:
            import litellm
            litellm.model_cost = new_db
        except ImportError:
            pass

        return True, f"已更新模型数据库: {model_count} 个模型 (缓存: {cache_file})"
    except Exception as e:
        return False, f"更新失败: {e}"


# ===== CLI 构建 =====

def build_cli():
    """构建子命令 CLI"""
    parser = argparse.ArgumentParser(
        prog="multi_ai_assistant",
        description="Multi AI Assistant - 通过快捷键在任意界面调用 AI 模型",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", type=str, default=None,
                        help="指定配置文件路径")
    parser.add_argument("--log-level", type=str, default=None,
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--version", "-V", action="version",
                        version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", help="子命令")

    # --- run (默认) ---
    p_run = sub.add_parser("run", help="启动助手 (默认)")
    p_run.add_argument("--web", action="store_true", help="同时启动 Web 管理界面")
    p_run.add_argument("--web-port", type=int, default=None)
    p_run.add_argument("--web-host", type=str, default=None)

    # --- model ---
    p_model = sub.add_parser("model", help="模型管理")
    model_sub = p_model.add_subparsers(dest="model_cmd")

    p_model_list = model_sub.add_parser("list", help="列出已配置的模型 (支持搜索过滤)")
    p_model_list.add_argument("query", nargs="?", default="", help="搜索关键词 (模型ID/标识符)")
    p_model_list.add_argument("--channel", "-c", default="", help="按渠道过滤")

    p_model_update = model_sub.add_parser("update", help="从渠道同步模型列表到配置")
    p_model_update.add_argument("--channel", "-c", default="", help="只更新指定渠道 (默认全部)")

    model_sub.add_parser("update-db", help="更新 litellm 内置模型数据库 (从 GitHub)")

    p_model_rm = model_sub.add_parser("remove", help="删除模型")
    p_model_rm.add_argument("key", help="模型名称")

    p_model_search = model_sub.add_parser("search", help="搜索 litellm 支持的模型")
    p_model_search.add_argument("query", nargs="?", default="", help="搜索关键词 (如 gpt, claude, gemini)")
    p_model_search.add_argument("--provider", "-p", default="", help="限定提供商 (如 openai, anthropic)")
    p_model_search.add_argument("--limit", "-n", type=int, default=30, help="最大显示条数")

    model_sub.add_parser("providers", help="列出所有支持的 AI 提供商")

    # --- hotkey ---
    p_hotkey = sub.add_parser("hotkey", help="快捷键管理")
    hotkey_sub = p_hotkey.add_subparsers(dest="hotkey_cmd")

    hotkey_sub.add_parser("list", help="列出所有快捷键")

    p_hk_set = hotkey_sub.add_parser("set", help="设置快捷键")
    p_hk_set.add_argument("key", help="快捷键组合 (如 f9+g)")
    p_hk_set.add_argument("action", help="动作类型 (chat/role/cancel/reload/exit)")
    p_hk_set.add_argument("target", nargs="?", default="", help="动作目标 (模型名/角色名)")

    p_hk_rm = hotkey_sub.add_parser("remove", help="删除快捷键")
    p_hk_rm.add_argument("key", help="快捷键组合")

    # --- role ---
    p_role = sub.add_parser("role", help="角色管理")
    role_sub = p_role.add_subparsers(dest="role_cmd")

    role_sub.add_parser("list", help="列出所有角色")

    p_role_add = role_sub.add_parser("add", help="添加角色")
    p_role_add.add_argument("key", help="角色名称")
    p_role_add.add_argument("base_model", help="基础模型 key")
    p_role_add.add_argument("prompt_key", help="Prompt 模板 key")

    p_role_rm = role_sub.add_parser("remove", help="删除角色")
    p_role_rm.add_argument("key", help="角色名称")

    # --- prompt ---
    p_prompt = sub.add_parser("prompt", help="Prompt 模板管理")
    prompt_sub = p_prompt.add_subparsers(dest="prompt_cmd")

    prompt_sub.add_parser("list", help="列出所有 Prompt")

    p_prompt_set = prompt_sub.add_parser("set", help="设置 Prompt")
    p_prompt_set.add_argument("key", help="Prompt 名称")
    p_prompt_set.add_argument("value", help="Prompt 内容")

    p_prompt_rm = prompt_sub.add_parser("remove", help="删除 Prompt")
    p_prompt_rm.add_argument("key", help="Prompt 名称")

    # --- channel ---
    p_channel = sub.add_parser("channel", help="渠道管理 (自定义 API 端点)")
    channel_sub = p_channel.add_subparsers(dest="channel_cmd")

    channel_sub.add_parser("list", help="列出所有渠道")

    p_ch_add = channel_sub.add_parser("add", help="添加渠道")
    p_ch_add.add_argument("name", help="渠道名称 (如 my_api)")
    p_ch_add.add_argument("--api-base", default="", help="API 地址 (直接指定)")
    p_ch_add.add_argument("--api-base-env", default="", help="API 地址环境变量名")
    p_ch_add.add_argument("--api-key", default="", help="API 密钥 (直接指定)")
    p_ch_add.add_argument("--api-key-env", default="", help="API 密钥环境变量名")
    p_ch_add.add_argument("--provider", default="custom", help="渠道类型 (默认 custom)")
    p_ch_add.add_argument("--description", "-d", default="", help="渠道描述")

    p_ch_rm = channel_sub.add_parser("remove", help="删除渠道")
    p_ch_rm.add_argument("name", help="渠道名称")

    p_ch_test = channel_sub.add_parser("test", help="测试渠道连接")
    p_ch_test.add_argument("name", help="渠道名称")

    p_ch_models = channel_sub.add_parser("models", help="查看渠道可用模型列表")
    p_ch_models.add_argument("name", help="渠道名称")
    p_ch_models.add_argument("query", nargs="?", default="", help="搜索关键词")

    # --- config ---
    p_config = sub.add_parser("config", help="配置管理")
    config_sub = p_config.add_subparsers(dest="config_cmd")
    config_sub.add_parser("init", help="初始化用户配置文件")
    config_sub.add_parser("path", help="显示配置文件路径")
    config_sub.add_parser("check", help="检查模型环境变量状态")
    config_sub.add_parser("edit", help="用编辑器打开配置文件")
    config_sub.add_parser("show", help="显示当前合并后的完整配置")

    return parser


# ===== 子命令处理函数 =====

def cmd_model(args, config: ConfigManager):
    if args.model_cmd == "list" or args.model_cmd is None:
        models = config.models
        if not models:
            print("没有配置模型。")
            print("\n通过渠道添加模型:")
            print("  maa channel add my_api --api-base-env MY_API_BASE --api-key-env MY_API_KEY")
            print("  maa model update             # 从渠道同步模型列表")
            return

        # 过滤
        query = getattr(args, "query", "") or ""
        channel_filter = getattr(args, "channel", "") or ""
        query_lower = query.lower()

        filtered = {}
        for key, conf in models.items():
            # 按渠道过滤
            if channel_filter:
                model_provider = conf.get("provider", "")
                if channel_filter != model_provider and not key.startswith(f"{channel_filter}/"):
                    continue
            # 按关键词搜索 (匹配 key 或 model_id)
            if query_lower:
                model_id = conf.get("model", "")
                if query_lower not in key.lower() and query_lower not in model_id.lower():
                    continue
            filtered[key] = conf

        if not filtered:
            print(f"未找到匹配的模型。")
            if query or channel_filter:
                print(f"  当前共 {len(models)} 个模型，尝试放宽搜索条件")
            return

        # 按渠道分组显示
        by_channel = {}
        for key, conf in filtered.items():
            ch = conf.get("provider", "litellm")
            by_channel.setdefault(ch, []).append((key, conf))

        filter_desc = []
        if query:
            filter_desc.append(f"关键词='{query}'")
        if channel_filter:
            filter_desc.append(f"渠道='{channel_filter}'")
        title = f"已配置模型 (共 {len(filtered)} 个"
        if filter_desc:
            title += f", {', '.join(filter_desc)}"
        title += ")"

        print(f"\n=== {title} ===")
        for ch_name, ch_models in by_channel.items():
            print(f"\n  [{ch_name}] ({len(ch_models)} 个模型)")
            for key, conf in ch_models:
                model_id = conf.get("model", "?")
                extras = []
                if conf.get("enable_search"):
                    extras.append("search")
                if conf.get("enable_reasoning"):
                    extras.append("reasoning")
                if not conf.get("stream", True):
                    extras.append("no-stream")
                extra_str = f"  ({', '.join(extras)})" if extras else ""
                print(f"    {key:<40} {model_id}{extra_str}")
        print()

    elif args.model_cmd == "update":
        channel_filter = getattr(args, "channel", "") or ""
        channels = config.get("channels", {})

        if not channels:
            print("没有配置渠道。请先添加渠道:")
            print("  maa channel add my_api --api-base-env MY_API_BASE --api-key-env MY_API_KEY")
            return

        target_channels = {}
        if channel_filter:
            if channel_filter not in channels:
                print(f"✗ 渠道 '{channel_filter}' 不存在")
                return
            target_channels[channel_filter] = channels[channel_filter]
        else:
            target_channels = channels

        total_added = 0
        total_models = 0

        for ch_name, ch_conf in target_channels.items():
            print(f"正在获取渠道 '{ch_name}' 的模型列表...")
            models_list, err = _fetch_channel_models(config, ch_name)
            if err:
                print(f"  ✗ {ch_name}: {err}")
                continue

            added, count = _sync_channel_models_to_config(config, ch_name, models_list)
            total_added += added
            total_models += count
            print(f"  ✓ {ch_name}: {count} 个模型 (新增 {added})")

        if total_added > 0:
            config.save_user_config()

        print(f"\n同步完成: 共 {total_models} 个模型, 新增 {total_added} 个")
        if total_added > 0:
            print(f"\n查看模型:  maa model list")
            print(f"绑定快捷键: maa hotkey set f9+g chat <模型key>")

    elif args.model_cmd == "update-db":
        ok, msg = _update_litellm_db()
        if ok:
            print(f"✓ {msg}")
        else:
            print(f"✗ {msg}")

    elif args.model_cmd == "remove":
        models = config.models
        if args.key not in models:
            print(f"✗ 模型 '{args.key}' 不存在")
            return
        # 直接操作字典，避免 key 含 '.' 时路径解析错误
        user_models = config.user_config.get("models", {})
        if args.key in user_models:
            del user_models[args.key]
            config._rebuild()
            config.save_user_config()
        else:
            config.load()
        print(f"✓ 模型已删除: {args.key}")

    elif args.model_cmd == "search":
        if not args.query and not args.provider:
            print("用法: maa model search <关键词> [--provider <提供商>]")
            print("\n示例:")
            print("  model search gpt                 # 搜索含 'gpt' 的模型")
            print("  model search --provider openai   # 列出 OpenAI 所有模型")
            print("  model search claude              # 搜索 Claude 模型")
            print("  model search flash -p google     # Google 的 flash 模型")
            print("\n查看所有提供商: maa model providers")
            return

        results = _search_models(
            query=args.query,
            provider=args.provider,
            limit=args.limit,
        )
        if not results:
            print(f"未找到匹配的模型。")
            if args.provider:
                print(f"  提示: 使用 'model providers' 查看所有支持的提供商名称")
            return

        filter_desc = []
        if args.query:
            filter_desc.append(f"关键词='{args.query}'")
        if args.provider:
            filter_desc.append(f"提供商='{args.provider}'")
        print(f"\n=== 搜索结果 ({', '.join(filter_desc)}, 共 {len(results)} 条) ===")
        print(f"  {'模型标识符':<46} {'提供商':<16} {'上下文':<10} {'特性'}")
        print(f"  {'─' * 46} {'─' * 16} {'─' * 10} {'─' * 16}")
        for r in results:
            ctx = f"{r['max_input']//1000}K" if r['max_input'] else "-"
            features = []
            if r['vision']:
                features.append("vision")
            if r['function_calling']:
                features.append("tools")
            feat_str = ", ".join(features) if features else ""
            print(f"  {r['model']:<46} {r['provider']:<16} {ctx:<10} {feat_str}")
        print(f"\n提示: 以上为 litellm 内置模型数据库。自定义渠道模型请使用:")
        print(f"  maa channel models <渠道名>     # 查看渠道可用模型")
        print(f"  maa model update-db             # 更新内置数据库")
        print()

    elif args.model_cmd == "providers":
        providers = _get_providers()
        if not providers:
            print("无法加载 litellm 模型数据库")
            return
        sorted_p = sorted(providers.items(), key=lambda x: x[1]["chat"], reverse=True)
        print(f"\n=== 支持的 AI 提供商 (共 {len(sorted_p)} 个) ===")
        print(f"  {'提供商':<24} {'Chat 模型':<12} {'总模型数'}")
        print(f"  {'─' * 24} {'─' * 12} {'─' * 10}")
        for name, counts in sorted_p:
            if counts["chat"] > 0:
                print(f"  {name:<24} {counts['chat']:<12} {counts['total']}")
        print(f"\n搜索某个提供商的模型:")
        print(f"  maa model search --provider openai")
        print()


def cmd_hotkey(args, config: ConfigManager):
    if args.hotkey_cmd == "list" or args.hotkey_cmd is None:
        hotkeys = config.hotkeys
        if not hotkeys:
            print("没有配置快捷键。")
            print("\n快速添加:")
            print("  maa hotkey set f9+g chat gpt")
            return
        print("\n=== 快捷键列表 ===")
        for hk, binding in hotkeys.items():
            action = binding.get("action", "")
            target = binding.get("target", "")
            desc = f"{action} → {target}" if target else action
            print(f"  {hk:<14} {desc}")
        print()

    elif args.hotkey_cmd == "set":
        # 验证 action 合法性
        valid_actions = {
            "chat", "role", "cancel", "reload", "exit",
        }
        if args.action not in valid_actions:
            print(f"✗ 无效的 action: '{args.action}'")
            print(f"  可用 action: {', '.join(sorted(valid_actions))}")
            return

        # 验证需要 target 的 action
        needs_target = {"chat", "role"}
        if args.action in needs_target and not args.target:
            print(f"✗ action '{args.action}' 需要指定 target")
            if args.action == "chat":
                models = config.models
                if models:
                    print(f"  可用模型: {', '.join(models.keys())}")
            elif args.action == "role":
                roles = config.roles
                if roles:
                    print(f"  可用角色: {', '.join(roles.keys())}")
            return

        # 验证 target 是否存在
        if args.action == "chat" and args.target and args.target not in config.models:
            print(f"⚠ 模型 '{args.target}' 尚未配置")
            print(f"  查看可用模型: maa model list")
            print(f"  从渠道同步:   maa model update")

        binding = {"action": args.action}
        if args.target:
            binding["target"] = args.target
        config.set(f"hotkeys.{args.key}", binding)
        config.save_user_config()
        desc = f"{args.action} → {args.target}" if args.target else args.action
        print(f"✓ 快捷键已设置: {args.key} → {desc}")

    elif args.hotkey_cmd == "remove":
        hotkeys = config.hotkeys
        if args.key not in hotkeys:
            print(f"✗ 快捷键 '{args.key}' 不存在")
            return
        user_hotkeys = config.user_config.get("hotkeys", {})
        if args.key in user_hotkeys:
            del user_hotkeys[args.key]
            config.save_user_config()
        config.load()
        print(f"✓ 快捷键已删除: {args.key}")


def cmd_role(args, config: ConfigManager):
    if args.role_cmd == "list" or args.role_cmd is None:
        roles = config.roles
        if not roles:
            print("没有配置角色。")
            print("\n快速添加:")
            print("  maa prompt set trans_en '翻译为英文，只输出翻译结果。'")
            print("  maa role add translator gpt trans_en")
            return
        print("\n=== 角色列表 ===")
        for key, conf in roles.items():
            print(f"  {key:<20} base_model={conf.get('base_model', '?'):<12} prompt_key={conf.get('prompt_key', '?')}")
        print()

    elif args.role_cmd == "add":
        if args.base_model not in config.models:
            print(f"✗ 基础模型 '{args.base_model}' 不存在")
            print(f"  查看可用模型: maa model list")
            return
        if args.prompt_key not in config.prompts:
            print(f"⚠ Prompt '{args.prompt_key}' 不存在，请先添加:")
            print(f"  maa prompt set {args.prompt_key} '<内容>'")
        config.set(f"roles.{args.key}", {
            "base_model": args.base_model,
            "prompt_key": args.prompt_key,
        })
        config.save_user_config()
        print(f"✓ 角色已添加: {args.key}")

    elif args.role_cmd == "remove":
        roles = config.roles
        if args.key not in roles:
            print(f"✗ 角色 '{args.key}' 不存在")
            return
        user_roles = config.user_config.get("roles", {})
        if args.key in user_roles:
            del user_roles[args.key]
            config.save_user_config()
        config.load()
        print(f"✓ 角色已删除: {args.key}")


def cmd_prompt(args, config: ConfigManager):
    if args.prompt_cmd == "list" or args.prompt_cmd is None:
        prompts = config.prompts
        if not prompts:
            print("没有配置 Prompt。")
            print("\n快速添加:")
            print("  maa prompt set trans_en '将以下文本翻译成英文，只输出翻译结果。'")
            return
        print("\n=== Prompt 列表 ===")
        for key, value in prompts.items():
            display = value[:60] + "..." if len(value) > 60 else value
            print(f"  {key:<24} {display}")
        print()

    elif args.prompt_cmd == "set":
        config.set(f"prompts.{args.key}", args.value)
        config.save_user_config()
        print(f"✓ Prompt 已设置: {args.key}")

    elif args.prompt_cmd == "remove":
        prompts = config.prompts
        if args.key not in prompts:
            print(f"✗ Prompt '{args.key}' 不存在")
            return
        user_prompts = config.user_config.get("prompts", {})
        if args.key in user_prompts:
            del user_prompts[args.key]
            config.save_user_config()
        config.load()
        print(f"✓ Prompt 已删除: {args.key}")


def cmd_channel(args, config: ConfigManager):
    if args.channel_cmd == "list" or args.channel_cmd is None:
        channels = config.get("channels", {})
        if not channels:
            print("没有配置渠道。")
            print("\n渠道用于连接自定义/中转 API 端点。标准提供商 (OpenAI, Gemini 等) 无需配置渠道。")
            print("\n添加自定义渠道:")
            print("  maa channel add my_api --api-base-env MY_API_BASE --api-key-env MY_API_KEY")
            return
        print("\n=== 渠道列表 ===")
        for name, conf in channels.items():
            ch_type = conf.get("type", "custom")
            provider = conf.get("provider", "")
            desc = conf.get("description", "")
            healthy = conf.get("healthy")
            status = "✓" if healthy is True else ("✗" if healthy is False else "?")

            extras = []
            if conf.get("api_base_env"):
                extras.append(f"base_env={conf['api_base_env']}")
            if conf.get("api_base"):
                base_display = conf['api_base'][:30] + "..." if len(conf['api_base']) > 30 else conf['api_base']
                extras.append(f"base={base_display}")
            if conf.get("api_key_env"):
                extras.append(f"key_env={conf['api_key_env']}")
            extra_str = f"  ({', '.join(extras)})" if extras else ""

            print(f"  {status} {name:<16} [{ch_type}] {desc}{extra_str}")
        print()

    elif args.channel_cmd == "add":
        if not args.api_base and not args.api_base_env:
            print("✗ 请指定 --api-base 或 --api-base-env (至少一个)")
            return
        if not args.api_key and not args.api_key_env:
            print("✗ 请指定 --api-key 或 --api-key-env (至少一个)")
            return

        channel_conf = {
            "type": "custom" if args.provider == "custom" else "standard",
            "provider": args.provider,
            "description": args.description or f"自定义渠道: {args.name}",
            "healthy": None,
            "last_check": None,
        }
        if args.api_base:
            channel_conf["api_base"] = args.api_base
        if args.api_base_env:
            channel_conf["api_base_env"] = args.api_base_env
        if args.api_key:
            channel_conf["api_key"] = args.api_key
        if args.api_key_env:
            channel_conf["api_key_env"] = args.api_key_env

        config.set(f"channels.{args.name}", channel_conf)
        config.save_user_config()
        print(f"✓ 渠道已添加: {args.name}")

        # 自动获取模型列表并同步
        print(f"正在获取模型列表...")
        models_list, err = _fetch_channel_models(config, args.name)
        if err:
            print(f"  ⚠ 获取模型列表失败: {err}")
            print(f"  稍后可手动同步: maa model update --channel {args.name}")
        elif models_list:
            added, count = _sync_channel_models_to_config(config, args.name, models_list)
            config.save_user_config()
            print(f"  ✓ 已同步 {count} 个模型 (新增 {added})")
            print(f"\n查看模型:  maa model list --channel {args.name}")
            print(f"绑定快捷键: maa hotkey set f9+g chat {args.name}/<model-id>")
        else:
            print(f"  ⚠ 渠道返回空模型列表")

    elif args.channel_cmd == "remove":
        channels = config.get("channels", {})
        if args.name not in channels:
            print(f"✗ 渠道 '{args.name}' 不存在")
            return

        # 直接操作字典，避免 key 含 '.' 时路径解析错误
        user_models = config.user_config.get("models", {})
        removed_models = [k for k, v in user_models.items()
                          if isinstance(v, dict) and v.get("provider") == args.name]
        for mk in removed_models:
            del user_models[mk]

        # 级联删除相关快捷键
        removed_hotkeys = _remove_channel_hotkeys(config, args.name)

        user_channels = config.user_config.get("channels", {})
        if args.name in user_channels:
            del user_channels[args.name]

        config._rebuild()
        config.save_user_config()
        
        # 统一报告删除结果
        messages = []
        if removed_models:
            messages.append(f"删除 {len(removed_models)} 个关联模型")
        if removed_hotkeys:
            messages.append(f"删除 {len(removed_hotkeys)} 个相关快捷键")
        
        if messages:
            detail = f" (同时{', '.join(messages)})"
            print(f"✓ 渠道已删除: {args.name}{detail}")
        else:
            print(f"✓ 渠道已删除: {args.name}")

    elif args.channel_cmd == "test":
        channels = config.get("channels", {})
        if args.name not in channels:
            print(f"✗ 渠道 '{args.name}' 不存在")
            return

        print(f"正在测试渠道 '{args.name}'...")
        models_list, err = _fetch_channel_models(config, args.name)
        if err:
            print(f"✗ 连接失败: {err}")
        else:
            print(f"✓ 连接成功! 可用模型: {len(models_list)} 个")

    elif args.channel_cmd == "models":
        channels = config.get("channels", {})
        if args.name not in channels:
            print(f"✗ 渠道 '{args.name}' 不存在")
            return

        print(f"正在获取渠道 '{args.name}' 的模型列表...")
        models_list, err = _fetch_channel_models(config, args.name)
        if err:
            print(f"✗ {err}")
            return

        # 搜索过滤
        query = getattr(args, "query", "") or ""
        if query:
            query_lower = query.lower()
            models_list = [m for m in models_list if query_lower in m["id"].lower()]

        if not models_list:
            print(f"未找到匹配的模型")
            return

        print(f"\n=== 渠道 '{args.name}' 可用模型 (共 {len(models_list)} 个) ===")
        # 标记哪些已添加到配置
        configured_models = config.models or {}
        for m in models_list:
            model_key = f"{args.name}/{m['id']}"
            status = "✓" if model_key in configured_models else " "
            print(f"  {status} {m['id']}")
        print(f"\n✓ = 已同步到配置")
        print(f"同步全部: maa model update --channel {args.name}")


def cmd_config(args, config: ConfigManager):
    if args.config_cmd == "init":
        config.init_user_config()
        print(f"✓ 用户配置已初始化: {config.user_config_path}")

    elif args.config_cmd == "path":
        print(f"默认配置: {config._default_config_path}")
        print(f"用户配置: {config.user_config_path}")
        exists = "已创建" if config.user_config_path.exists() else "未创建"
        print(f"用户配置状态: {exists}")

    elif args.config_cmd == "check":
        models = config.models
        if not models:
            print("没有配置模型。请先添加渠道并同步模型:")
            print("  maa channel add my_api --api-base-env MY_API_BASE --api-key-env MY_API_KEY")
            print("  maa model update")
            return
        print(f"\n=== 模型环境变量检查 (共 {len(models)} 个模型) ===")
        for key, conf in models.items():
            model_id = conf.get("model", "?")
            api_key_env = conf.get("api_key_env", "")
            api_base_env = conf.get("api_base_env", "")

            issues = []
            if api_key_env and not os.getenv(api_key_env):
                issues.append(f"{api_key_env} 未设置")
            if api_base_env and not os.getenv(api_base_env):
                issues.append(f"{api_base_env} 未设置")

            if issues:
                print(f"  ✗ {key:<14} {model_id:<30} {', '.join(issues)}")
            elif api_key_env or api_base_env:
                print(f"  ✓ {key:<14} {model_id}")
            else:
                # 检查 litellm 标准环境变量
                db = _get_litellm_models()
                resolved = _resolve_model_in_db(db, model_id)
                if resolved:
                    provider = resolved.get("litellm_provider", "")
                    env_hint = _provider_env_hint(provider)
                    env_ok = bool(os.getenv(env_hint)) if env_hint else None
                    if env_ok:
                        print(f"  ✓ {key:<14} {model_id:<30} ({env_hint})")
                    elif env_hint:
                        print(f"  ✗ {key:<14} {model_id:<30} 需要设置 {env_hint}")
                    else:
                        print(f"  ? {key:<14} {model_id}")
                else:
                    print(f"  ? {key:<14} {model_id:<30} (未在 litellm 数据库中)")
        print()

    elif args.config_cmd == "edit":
        if not config.user_config_path.exists():
            config.init_user_config()
        editor = os.environ.get("EDITOR", "nano")
        os.system(f"{editor} {config.user_config_path}")

    elif args.config_cmd == "show":
        import yaml
        print(yaml.dump(config.config, default_flow_style=False, allow_unicode=True, sort_keys=False))

    else:
        print("用法: maa config {init|path|check|edit|show}")


def _provider_env_hint(provider: str) -> str:
    """根据 litellm provider 名返回对应的环境变量名"""
    _map = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GEMINI_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "groq": "GROQ_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "cohere": "COHERE_API_KEY",
        "cohere_chat": "COHERE_API_KEY",
        "mistral": "MISTRAL_API_KEY",
        "azure": "AZURE_API_KEY",
        "together_ai": "TOGETHERAI_API_KEY",
        "fireworks_ai": "FIREWORKS_AI_API_KEY",
        "replicate": "REPLICATE_API_KEY",
        "perplexity": "PERPLEXITYAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "xai": "XAI_API_KEY",
        "cerebras": "CEREBRAS_API_KEY",
        "dashscope": "DASHSCOPE_API_KEY",
    }
    return _map.get(provider, "")


# ===== 主程序 =====

def cmd_run(args, config: ConfigManager):
    """启动助手主程序"""
    global _factory, _hotkey_mgr

    _factory = ModelFactory(config)
    _hotkey_mgr = HotkeyManager(config, _factory)
    _hotkey_mgr.set_reload_callback(_do_reload)

    # Web 管理界面
    web_enabled = getattr(args, "web", False) or config.web_config.get("enabled", False)
    if web_enabled:
        web_host = getattr(args, "web_host", None) or config.web_config.get("host", "127.0.0.1")
        web_port = getattr(args, "web_port", None) or config.web_config.get("port", 8199)
        try:
            from .web.server import start_web_server
            start_web_server(config, _factory, host=web_host, port=web_port,
                             reload_callback=_do_reload)
            print(f"Web 管理界面: http://{web_host}:{web_port}")
        except Exception as e:
            logger.error(f"Web 启动失败: {e}")

    # 首次运行引导
    has_user_config = config.user_config_path.exists()
    has_models = bool(config.models)
    has_chat_hotkeys = any(
        b.get("action") == "chat" for b in config.hotkeys.values()
    )

    if not has_user_config or not has_models:
        print()
        print("=" * 52)
        print("  欢迎使用 Multi AI Assistant!")
        print("  支持 100+ AI 提供商，通过快捷键随处调用")
        print("=" * 52)
        print()
        print("快速开始 (3 步):")
        print()
        print("  1. 添加渠道 (设置 API 端点和密钥):")
        print("     maa channel add my_api --api-base-env MY_API_BASE --api-key-env MY_API_KEY")
        print()
        print("  2. 同步模型列表:")
        print("     maa model update")
        print()
        print("  3. 绑定快捷键并启动:")
        print("     maa model list                        # 查看可用模型")
        print("     maa hotkey set f9+g chat my_api/gpt-4o  # 绑定快捷键")
        print("     maa run --web                         # 启动")
        print()
        if not has_models:
            print("提示: 当前没有配置任何模型，程序将以最小配置运行。")
            print()

    print(f"=== Multi AI Assistant v{__version__} ===")
    _hotkey_mgr.register_all()
    _hotkey_mgr.print_hotkeys()

    _hotkey_mgr.wait_for_exit()
    _hotkey_mgr.unregister_all()
    print("程序已退出")


def AI_Assistant():
    """主入口"""
    parser = build_cli()
    args = parser.parse_args()

    # 加载配置
    config = get_config(args.config)

    # 设置日志
    log_level = args.log_level or config.logging_config.get("level", "INFO")
    log_file = config.logging_config.get("file", "")
    setup_logging(log_level, log_file)

    # 路由子命令
    cmd = args.command

    if cmd == "model":
        cmd_model(args, config)
    elif cmd == "hotkey":
        cmd_hotkey(args, config)
    elif cmd == "role":
        cmd_role(args, config)
    elif cmd == "prompt":
        cmd_prompt(args, config)
    elif cmd == "channel":
        cmd_channel(args, config)
    elif cmd == "config":
        cmd_config(args, config)
    elif cmd == "run" or cmd is None:
        cmd_run(args, config)
    else:
        parser.print_help()


if __name__ == "__main__":
    AI_Assistant()
