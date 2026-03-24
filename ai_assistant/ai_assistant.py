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

def _get_litellm_models():
    """获取 litellm 支持的模型数据库"""
    try:
        import litellm
        return litellm.model_cost
    except Exception:
        return {}


def _search_models(query="", provider="", mode="chat", limit=50):
    """搜索 litellm 支持的模型

    Args:
        query: 模型名关键词 (模糊匹配)
        provider: 限定提供商
        mode: 模型类型过滤 (chat/embedding/image_generation 等)
        limit: 最大返回条数
    """
    db = _get_litellm_models()
    results = []
    query_lower = query.lower()
    provider_lower = provider.lower()

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

        results.append({
            "model": model_id,
            "provider": m_provider,
            "mode": m_mode,
            "max_input": info.get("max_input_tokens", 0),
            "max_output": info.get("max_output_tokens", 0),
            "vision": info.get("supports_vision", False),
            "function_calling": info.get("supports_function_calling", False),
        })

        if len(results) >= limit:
            break

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

    model_sub.add_parser("list", help="列出已配置的模型")

    p_model_add = model_sub.add_parser("add", help="添加模型")
    p_model_add.add_argument("key", help="模型名称 (如 gpt)")
    p_model_add.add_argument("model_id", help="litellm 模型标识符 (如 gpt-4o-mini)")
    p_model_add.add_argument("--api-key-env", default="", help="API 密钥环境变量名")
    p_model_add.add_argument("--api-base-env", default="", help="API 地址环境变量名")
    p_model_add.add_argument("--no-stream", action="store_true", help="禁用流式输出")
    p_model_add.add_argument("--enable-search", action="store_true")
    p_model_add.add_argument("--enable-reasoning", action="store_true")

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
            print("\n快速添加:")
            print("  maa model search gpt         # 搜索可用模型")
            print("  maa model add gpt gpt-4o-mini  # 添加模型")
            return
        print("\n=== 已配置模型 ===")
        for key, conf in models.items():
            model_id = conf.get("model", "?")
            extras = []
            if conf.get("api_key_env"):
                extras.append(f"key={conf['api_key_env']}")
            if conf.get("api_base_env"):
                extras.append(f"base={conf['api_base_env']}")
            if conf.get("enable_search"):
                extras.append("search")
            if conf.get("enable_reasoning"):
                extras.append("reasoning")
            if not conf.get("stream", True):
                extras.append("no-stream")
            extra_str = f"  ({', '.join(extras)})" if extras else ""
            print(f"  {key:<16} {model_id}{extra_str}")
        print()

    elif args.model_cmd == "add":
        model_conf = {"model": args.model_id}
        if args.api_key_env:
            model_conf["api_key_env"] = args.api_key_env
        if args.api_base_env:
            model_conf["api_base_env"] = args.api_base_env
        if args.no_stream:
            model_conf["stream"] = False
        if args.enable_search:
            model_conf["enable_search"] = True
        if args.enable_reasoning:
            model_conf["enable_reasoning"] = True

        # 在 litellm 数据库中查找模型信息（仅作提示，不阻止添加）
        db = _get_litellm_models()
        resolved = _resolve_model_in_db(db, args.model_id)
        if resolved:
            provider = resolved.get("litellm_provider", "unknown")
            print(f"✓ 模型已添加: {args.key} → {args.model_id} (provider: {provider})")
        elif args.api_base_env:
            print(f"✓ 模型已添加: {args.key} → {args.model_id} (自定义端点)")
        else:
            # litellm 可路由的模型远多于 model_cost 数据库中的，直接添加即可
            print(f"✓ 模型已添加: {args.key} → {args.model_id}")

        config.set(f"models.{args.key}", model_conf)
        config.save_user_config()

    elif args.model_cmd == "remove":
        models = config.models
        if args.key not in models:
            print(f"✗ 模型 '{args.key}' 不存在")
            return
        user_models = config.user_config.get("models", {})
        if args.key in user_models:
            del user_models[args.key]
            config.save_user_config()
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
        print(f"\n添加模型: maa model add <名称> <模型标识符>")
        print(f"  示例: maa model add gpt {results[0]['model']}")
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
            print(f"⚠ 模型 '{args.target}' 尚未配置，请先添加: maa model add {args.target} <model_id>")

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
            print(f"✗ 基础模型 '{args.base_model}' 不存在，请先添加:")
            print(f"  maa model add {args.base_model} <model_id>")
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
        print(f"  接下来添加使用此渠道的模型:")
        print(f"  maa model add my_model openai/<model-name> --api-base-env {args.api_base_env or 'API_BASE'} --api-key-env {args.api_key_env or 'API_KEY'}")

    elif args.channel_cmd == "remove":
        channels = config.get("channels", {})
        if args.name not in channels:
            print(f"✗ 渠道 '{args.name}' 不存在")
            return
        config.delete(f"channels.{args.name}")
        config.save_user_config()
        print(f"✓ 渠道已删除: {args.name}")

    elif args.channel_cmd == "test":
        channels = config.get("channels", {})
        if args.name not in channels:
            print(f"✗ 渠道 '{args.name}' 不存在")
            return
        ch_conf = channels[args.name]
        api_base = ch_conf.get("api_base") or os.getenv(ch_conf.get("api_base_env", ""), "")
        api_key = ch_conf.get("api_key") or os.getenv(ch_conf.get("api_key_env", ""), "")

        if not api_base:
            env_name = ch_conf.get("api_base_env", "")
            print(f"✗ API Base 未设置 (环境变量 {env_name} 为空)")
            return
        if not api_key:
            env_name = ch_conf.get("api_key_env", "")
            print(f"✗ API Key 未设置 (环境变量 {env_name} 为空)")
            return

        print(f"正在测试渠道 '{args.name}'...")
        try:
            import requests
            headers = {"Authorization": f"Bearer {api_key}"}
            resp = requests.get(f"{api_base.rstrip('/')}/models", headers=headers, timeout=10)
            if resp.status_code == 200:
                models_data = resp.json()
                model_count = len(models_data.get("data", []))
                print(f"✓ 连接成功! 可用模型: {model_count} 个")
            else:
                print(f"✗ 连接失败: HTTP {resp.status_code}")
        except Exception as e:
            print(f"✗ 连接失败: {e}")


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
            print("没有配置模型。先添加模型: maa model add <key> <model_id>")
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
        print("  1. 设置 API 密钥:")
        print("     export OPENAI_API_KEY=sk-...")
        print()
        print("  2. 搜索并添加模型:")
        print("     maa model search gpt")
        print("     maa model add gpt gpt-4o-mini")
        print()
        print("  3. 绑定快捷键并启动:")
        print("     maa hotkey set f9+g chat gpt")
        print("     maa run")
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
