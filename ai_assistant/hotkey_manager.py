#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
快捷键管理模块 - 根据配置动态注册/注销快捷键，支持热重载
"""

import logging

import keyboard

from .utils import clear_possible_char, cancel_current_chat, send_system_notification
from .model_factory import ModelFactory

logger = logging.getLogger("multi_ai_assistant")


class HotkeyManager:
    """快捷键管理器

    根据配置文件中的 hotkeys 定义，动态注册/注销快捷键绑定。
    支持通过 reload action 实现配置热重载。
    """

    def __init__(self, config_manager, model_factory: ModelFactory):
        self.config = config_manager
        self.factory = model_factory
        self._registered_hotkeys = []
        self._exit_key = None
        self._reload_callback = None  # 外部注入的热重载回调

    def set_reload_callback(self, callback):
        """设置热重载回调函数（由 ai_assistant.py 注入）"""
        self._reload_callback = callback

    def _build_action_handler(self, action: str, target: str = ""):
        """根据 action 和 target 构建快捷键回调函数"""
        if action == "chat":
            def handler():
                clear_possible_char()
                model = self.factory.get_model(target)
                model.chat_thread()
            return handler

        elif action == "role":
            def handler():
                clear_possible_char()
                role = self.factory.get_role(target)
                role.chat_thread()
            return handler

        elif action == "cancel":
            return cancel_current_chat

        elif action == "reload":
            def handler():
                if self._reload_callback:
                    self._reload_callback()
                    send_system_notification("AI助手", "配置已重新加载")
                else:
                    logger.warning("未设置热重载回调")
            return handler

        elif action == "exit":
            return None

        else:
            logger.warning(f"不支持的 action: {action}")
            return None

    def register_all(self):
        """根据配置注册所有快捷键"""
        self.unregister_all()

        hotkeys_conf = self.config.hotkeys
        if not hotkeys_conf:
            logger.warning("配置中没有定义任何快捷键")
            return

        registered_count = 0
        for hotkey, binding in hotkeys_conf.items():
            action = binding.get("action", "")
            target = binding.get("target", "")

            if action == "exit":
                self._exit_key = hotkey
                continue

            handler = self._build_action_handler(action, target)
            if handler is None:
                continue

            try:
                keyboard.add_hotkey(hotkey, handler)
                self._registered_hotkeys.append(hotkey)
                registered_count += 1
            except Exception as e:
                logger.error(f"注册快捷键失败 {hotkey}: {e}")

        logger.info(f"已注册 {registered_count} 个快捷键")

    def unregister_all(self):
        """注销所有已注册的快捷键"""
        for hotkey in self._registered_hotkeys:
            try:
                keyboard.remove_hotkey(hotkey)
            except Exception:
                pass
        self._registered_hotkeys.clear()

    def wait_for_exit(self):
        """等待退出快捷键"""
        if self._exit_key:
            print(f"按 {self._exit_key} 退出程序")
            keyboard.wait(self._exit_key)
        else:
            print("按 Ctrl+C 退出")
            try:
                keyboard.wait()
            except KeyboardInterrupt:
                pass

    def print_hotkeys(self):
        """打印当前所有快捷键绑定"""
        hotkeys_conf = self.config.hotkeys
        if not hotkeys_conf:
            print("没有配置快捷键")
            return

        print("\n=== 快捷键列表 ===")
        groups = {}
        for hotkey, binding in hotkeys_conf.items():
            action = binding.get("action", "unknown")
            if action not in groups:
                groups[action] = []
            groups[action].append((hotkey, binding))

        action_labels = {
            "chat": "模型对话",
            "role": "角色对话",
            "cancel": "取消当前操作",
            "reload": "热重载配置",
            "exit": "退出程序",
        }

        for action, items in groups.items():
            label = action_labels.get(action, action)
            print(f"\n  [{label}]")
            for hotkey, binding in items:
                target = binding.get("target", "")
                if target:
                    print(f"    {hotkey:<12} → {target}")
                else:
                    print(f"    {hotkey:<12}")
        print()
