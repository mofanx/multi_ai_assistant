#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
AI助手基础模块 - 提供通用工具函数和基类
所有公共函数已迁移到 utils.py，此处保留导出以保持向后兼容。
"""

import threading
import logging

from ..utils import (
    clear_possible_char,
    get_clipboard_content,
    type_result,
    cancel_current_chat,
    reset_chat_state,
    mask_sensitive_info,
    current_chat_active,
    stop_event,
)

logger = logging.getLogger("multi_ai_assistant")

# 重新导出，保持 from .base import ... 的兼容性
__all__ = [
    'clear_possible_char',
    'get_clipboard_content',
    'type_result',
    'cancel_current_chat',
    'current_chat_active',
    'stop_event',
    'AIAssistantBase',
]


class AIAssistantBase:
    """AI助手基类，定义通用接口和方法"""
    
    def __init__(self, model_name=None):
        """初始化AI助手
        
        Args:
            model_name: 模型名称
        """
        self.model_name = model_name
    
    def get_api_info(self):
        """获取API信息，子类必须实现此方法"""
        raise NotImplementedError("子类必须实现get_api_info方法")
    
    def chat(self, content=None):
        """与模型进行对话，子类必须实现此方法
        
        Args:
            content: 对话内容，如果为None则从剪贴板获取
        """
        raise NotImplementedError("子类必须实现chat方法")
    
    def chat_thread(self):
        """创建一个新线程来执行聊天功能"""
        reset_chat_state()
        
        chat_thread = threading.Thread(target=self.chat)
        chat_thread.daemon = True
        chat_thread.start()
    
    @staticmethod
    def mask_sensitive_info(info):
        """掩盖敏感信息，只显示前4位和后4位
        
        Args:
            info: 需要掩盖的敏感信息
            
        Returns:
            掩盖后的信息
        """
        return mask_sensitive_info(info)
