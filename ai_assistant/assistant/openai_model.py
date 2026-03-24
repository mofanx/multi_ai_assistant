#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
LiteLLM 统一模型实现模块
通过 litellm 支持 100+ 模型提供商（OpenAI、Gemini、Anthropic、Groq 等）
同时支持推理模型（QWQ 等）的 reasoning_content 输出
"""

import logging

import litellm

from .base import AIAssistantBase, get_clipboard_content, type_result, current_chat_active, stop_event

logger = logging.getLogger("multi_ai_assistant")

# 禁用 litellm 自带的冗长日志
litellm.suppress_debug_info = True


class OpenAIAssistant(AIAssistantBase):
    """基于 litellm 的统一 AI 助手

    通过 litellm 的模型路由能力，一套代码支持所有主流 AI 提供商。
    模型标识符使用 litellm 格式：
      - "gpt-4o-mini"             → OpenAI (自动识别)
      - "gemini/gemini-2.0-flash" → Google Gemini
      - "anthropic/claude-3-5-sonnet-20241022" → Anthropic
      - "groq/llama-3.1-70b"     → Groq
      - "openai/custom-model"    → 自定义 OpenAI 兼容 API（配合 api_base）

    详见: https://docs.litellm.ai/docs/providers
    """

    def __init__(self, model="gpt-4o-mini",
                 api_base=None, api_key=None,
                 stream=True, enable_search=False,
                 enable_reasoning=False, prompt="",
                 extra_params=None):
        """初始化助手

        Args:
            model: litellm 模型标识符 (如 "gpt-4o-mini", "gemini/gemini-2.0-flash")
            api_base: API 地址 (可选，用于自定义端点)
            api_key: API 密钥 (可选，litellm 自动读取标准环境变量)
            stream: 是否流式输出 (默认 True)
            enable_search: 是否启用联网搜索 (部分模型支持)
            enable_reasoning: 是否输出推理过程 (用于 QWQ 等推理模型)
            prompt: 系统提示词
            extra_params: 传递给 litellm.completion 的额外参数
        """
        super().__init__(model)

        self.model = model
        self.api_base = api_base
        self.api_key = api_key
        self.stream = stream
        self.enable_search = enable_search
        self.enable_reasoning = enable_reasoning
        self.prompt = prompt
        self.extra_params = extra_params or {}

    def chat(self, content=None):
        """与模型对话

        Args:
            content: 对话内容，如果为 None 则从剪贴板获取

        Returns:
            模型的回复内容，或错误时返回 None
        """
        # 从剪贴板获取用户输入
        user_input = content if content else get_clipboard_content()
        if not user_input:
            return

        logger.info(f"[{self.model}] 用户问题: {user_input[:80]}...")

        # 构建消息
        messages = []
        if self.prompt:
            messages.append({"role": "system", "content": self.prompt})
        messages.append({"role": "user", "content": user_input})

        # 构建 litellm 请求参数
        kwargs = {
            "model": self.model,
            "messages": messages,
            "stream": self.stream,
            **self.extra_params,
        }
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.enable_search:
            kwargs["enable_search"] = True

        # 检查停止事件
        if stop_event.is_set() or not current_chat_active:
            logger.info("对话已取消")
            return

        try:
            if self.stream:
                return self._stream_chat(kwargs)
            else:
                return self._sync_chat(kwargs)
        except Exception as e:
            logger.error(f"[{self.model}] 调用失败: {e}")
            return None

    def _stream_chat(self, kwargs):
        """流式对话"""
        full_reply = ""
        response = litellm.completion(**kwargs)

        for chunk in response:
            if stop_event.is_set() or not current_chat_active:
                logger.info("对话已取消，停止输出")
                return full_reply

            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta

            # 推理模型：输出推理过程
            if self.enable_reasoning:
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    print(reasoning, end="", flush=True)
                    type_result(reasoning)
                    full_reply += reasoning
                    continue

            # 普通内容输出
            text = delta.content or ""
            if text:
                print(text, end="", flush=True)
                type_result(text)
                full_reply += text

        logger.info(f"[{self.model}] 回复完成，共 {len(full_reply)} 字符")
        return full_reply

    def _sync_chat(self, kwargs):
        """非流式对话"""
        response = litellm.completion(**kwargs)
        reply = response.choices[0].message.content or ""

        print(reply)
        type_result(reply)

        logger.info(f"[{self.model}] 回复完成，共 {len(reply)} 字符")
        return reply
