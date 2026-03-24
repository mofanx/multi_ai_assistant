"""
Multi AI Assistant - 通过快捷键在任意界面调用 AI 大模型

基于 litellm 支持 100+ AI 提供商，专注于大模型对话能力。
通过 YAML 配置 + CLI 子命令灵活管理模型、快捷键和 Prompt。
"""

__version__ = "2.1.0"

from .ai_assistant import AI_Assistant
from .config import get_config, ConfigManager
from .utils import (
    clear_possible_char,
    get_clipboard_content,
    type_result,
    cancel_current_chat,
    send_system_notification,
)
from .assistant.openai_model import OpenAIAssistant

__all__ = [
    'AI_Assistant',
    'get_config',
    'ConfigManager',
    'clear_possible_char',
    'get_clipboard_content',
    'type_result',
    'cancel_current_chat',
    'send_system_notification',
    'OpenAIAssistant',
]