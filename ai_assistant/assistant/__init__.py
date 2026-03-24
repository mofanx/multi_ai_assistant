#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
AI助手模块包 - 基于 litellm 统一路由
专注于大模型对话能力，通过快捷键在任意界面调用
"""

from .base import AIAssistantBase, get_clipboard_content, type_result, cancel_current_chat, clear_possible_char
from .openai_model import OpenAIAssistant

__all__ = [
    'AIAssistantBase',
    'get_clipboard_content',
    'type_result',
    'cancel_current_chat',
    'clear_possible_char',
    'OpenAIAssistant',
]
