#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
公共工具函数模块 - 提取自各模块的重复代码，统一管理
"""

import os
import time
import platform
import subprocess
import threading
import logging

import pyclip
import keyboard

logger = logging.getLogger("multi_ai_assistant")

# ===== 全局状态 =====
current_chat_active = True
stop_event = threading.Event()


def clear_possible_char():
    """清除可能由快捷键输入的字符"""
    keyboard.send('backspace')
    time.sleep(0.1)


def get_clipboard_content():
    """从剪贴板获取内容

    Returns:
        剪贴板文本内容，获取失败返回 None
    """
    try:
        clipboard_data = pyclip.paste()

        if isinstance(clipboard_data, bytes):
            clipboard_content = clipboard_data.decode('utf-8', errors='replace')
        else:
            clipboard_content = str(clipboard_data)

        try:
            if '\\u' in clipboard_content:
                clipboard_content = clipboard_content.encode('utf-8').decode('unicode_escape')
        except Exception as e:
            logger.warning(f"无法还原unicode转义字符串: {e}")
            return None

        if not clipboard_content:
            logger.info("剪贴板内容为空！")
            return None

        return clipboard_content
    except Exception as e:
        logger.error(f"无法从剪贴板获取内容: {e}")
        return None


def type_result(text):
    """模拟键盘输入或粘贴操作，将文本输出到当前光标位置

    Args:
        text: 要输出的文本
    """
    config_paste = 1
    config_restore = 1

    if config_paste:
        # 保存剪切板
        try:
            temp = pyclip.paste()
            if isinstance(temp, bytes):
                temp = temp.decode('utf-8')
        except Exception:
            temp = ''

        # 复制结果
        pyclip.copy(text)

        # 粘贴结果
        if platform.system() == 'Darwin':
            keyboard.press(55)
            keyboard.press(9)
            keyboard.release(55)
            keyboard.release(9)
        else:
            keyboard.send('ctrl + v')

        # 还原剪贴板
        if config_restore:
            paste_delay = max(0.1, min(len(text) / 5000, 1.0))
            time.sleep(paste_delay)
            pyclip.copy(temp)
    else:
        keyboard.write(text)


def cancel_current_chat():
    """取消当前对话"""
    global current_chat_active
    current_chat_active = False
    stop_event.set()
    logger.info("当前对话已取消！")


def reset_chat_state():
    """重置对话状态为活跃"""
    global current_chat_active
    current_chat_active = True
    stop_event.clear()


def mask_sensitive_info(info):
    """掩盖敏感信息，只显示前4位和后4位

    Args:
        info: 需要掩盖的敏感信息

    Returns:
        掩盖后的信息
    """
    if not info:
        return None
    return info[:4] + "*" * (len(info) - 8) + info[-4:] if len(info) > 8 else "****"


def send_system_notification(title, message):
    """跨平台桌面通知

    Args:
        title: 通知标题
        message: 通知内容
    """
    system = platform.system()
    if system == "Linux":
        try:
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                current_user = os.environ.get('SUDO_USER')
                if current_user:
                    user_home = f"/home/{current_user}"
                    display = os.environ.get('DISPLAY', ':0')
                    xauthority = os.environ.get('XAUTHORITY', f"{user_home}/.Xauthority")
                    cmd = f"DISPLAY={display} XAUTHORITY={xauthority} notify-send '{title}' '{message}'"
                    subprocess.run(['su', current_user, '-c', cmd], check=True)
                else:
                    logger.warning("无法确定实际用户，通知可能无法显示")
            else:
                subprocess.run(['notify-send', title, message], check=True)
        except Exception as e:
            logger.warning(f"发送系统通知失败: {e}")
    else:
        logger.info(f"[通知] {title}: {message}")


def setup_logging(level="INFO", log_file=""):
    """配置日志系统

    Args:
        level: 日志级别 (DEBUG/INFO/WARNING/ERROR)
        log_file: 日志文件路径，留空不输出到文件
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    handlers = [logging.StreamHandler()]
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding='utf-8'))

    logging.basicConfig(
        level=log_level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=handlers,
        force=True
    )
