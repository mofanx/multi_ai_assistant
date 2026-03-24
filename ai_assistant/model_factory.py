#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
模型工厂模块 - 根据配置动态创建模型实例
基于 litellm 统一路由，不再需要独立的 provider_registry
"""

import os
import logging
from typing import Dict, Optional

logger = logging.getLogger("multi_ai_assistant")

try:
    from litellm import get_model_info
except ImportError:
    logger.warning("litellm.get_model_info 不可用，将使用备用检测方法")
    get_model_info = None


class ModelFactory:
    """模型实例工厂

    根据配置文件中的模型定义和角色定义，动态创建对应的助手实例。
    使用 litellm 统一路由，凭证从环境变量自动解析或通过配置指定。
    """

    def __init__(self, config_manager):
        """初始化模型工厂

        Args:
            config_manager: ConfigManager 实例
        """
        self.config = config_manager
        self._model_instances = {}
        self._role_instances = {}

    def get_model_type_info(self, model_name: str) -> Dict[str, str]:
        """获取模型类型信息
        
        Args:
            model_name: 模型名称
            
        Returns:
            包含模型类型信息的字典
        """
        result = {
            "type": "chat",
            "category": "💬 对话模型",
            "mode": "text",
            "description": "通用对话模型"
        }
        
        if get_model_info:
            try:
                # 尝试使用 LiteLLM 获取模型信息（离线工作）
                model_info = get_model_info(model_name)
                lite_mode = model_info.get("mode", "chat")
                lite_provider = model_info.get("litellm_provider", "")
                supports_vision = model_info.get("supports_vision", False)
                
                # 基于 mode 字段和特性进行分类
                if lite_mode == "embedding":
                    result["category"] = "� 嵌入模型"
                    result["description"] = "向量嵌入模型"
                elif lite_mode == "image":
                    result["category"] = "🎨 图像生成"
                    result["description"] = "图像生成模型"
                elif lite_mode == "audio":
                    result["category"] = "🔊 语音模型"
                    result["description"] = "音频处理模型"
                elif lite_mode == "chat" and supports_vision:
                    result["category"] = "🔍 多模态模型"
                    result["description"] = "多模态处理模型"
                elif lite_mode == "chat":
                    # 进一步细分聊天模型
                    if any(keyword in model_name.lower() for keyword in ["code", "codex", "copilot"]):
                        result["category"] = "💻 代码模型"
                        result["description"] = "代码生成模型"
                    elif any(keyword in model_name.lower() for keyword in ["translate", "translation"]):
                        result["category"] = "🌐 翻译模型"
                        result["description"] = "翻译模型"
                    else:
                        result["category"] = "💬 对话模型"
                        result["description"] = "通用对话模型"
                else:
                    # 其他 mode 类型，基于名称检测
                    logger.debug(f"未知模型模式: {lite_mode}, 使用备用检测")
                    return self._fallback_model_detection(model_name)
                
                result["type"] = lite_mode
                result["mode"] = lite_mode
                result["provider"] = lite_provider
                
            except Exception as e:
                logger.debug(f"获取模型信息失败: {e}, 使用备用检测方法")
                # 回退到基于名称的检测
                return self._fallback_model_detection(model_name)
        else:
            # 使用备用检测方法
            return self._fallback_model_detection(model_name)
            
        return result
    
    def _fallback_model_detection(self, model_name: str) -> Dict[str, str]:
        """备用模型类型检测方法（基于模型名称）"""
        model_name_lower = model_name.lower()
        
        # 基于模型名称的检测规则
        if any(keyword in model_name_lower for keyword in ["embedding", "embed"]):
            return {
                "type": "embedding",
                "category": "📊 嵌入模型", 
                "mode": "embedding",
                "description": "向量嵌入模型"
            }
        elif any(keyword in model_name_lower for keyword in ["image", "dall", "stable-diffusion", "midjourney", "sdxl"]):
            return {
                "type": "image",
                "category": "🎨 图像生成",
                "mode": "image", 
                "description": "图像生成模型"
            }
        elif any(keyword in model_name_lower for keyword in ["whisper", "tts", "speech", "audio", "voice"]):
            return {
                "type": "audio",
                "category": "🔊 语音模型",
                "mode": "audio",
                "description": "音频处理模型"
            }
        elif any(keyword in model_name_lower for keyword in ["code", "codex", "copilot"]):
            return {
                "type": "code",
                "category": "💻 代码模型",
                "mode": "code",
                "description": "代码生成模型"
            }
        elif any(keyword in model_name_lower for keyword in ["translate", "translation"]):
            return {
                "type": "translation",
                "category": "🌐 翻译模型",
                "mode": "translation",
                "description": "翻译模型"
            }
        elif any(keyword in model_name_lower for keyword in ["gemini", "gpt-4-vision", "claude-3", "multimodal"]):
            return {
                "type": "multimodal",
                "category": "🔍 多模态模型",
                "mode": "multimodal",
                "description": "多模态处理模型"
            }
        else:
            # 默认为对话模型
            return {
                "type": "chat",
                "category": "💬 对话模型",
                "mode": "chat",
                "description": "通用对话模型"
            }

    def _resolve_credentials(self, model_conf: dict) -> tuple:
        """从模型配置中解析 API 凭证

        优先从模型自身配置解析，若模型关联了渠道(provider)，
        则从渠道配置中补充 api_base 和 api_key。

        Returns:
            (api_base, api_key) 元组，可能为 (None, None)
        """
        api_key_env = model_conf.get("api_key_env", "")
        api_base_env = model_conf.get("api_base_env", "")
        api_key = os.getenv(api_key_env) if api_key_env else None
        api_base = os.getenv(api_base_env) if api_base_env else None

        # 如果模型关联了渠道，从渠道配置中补充凭证
        provider_name = model_conf.get("provider", "")
        if provider_name:
            channels = self.config.get("channels", {})
            channel_conf = channels.get(provider_name, {})
            if channel_conf:
                # 渠道的 api_base: 直接值 > 环境变量
                if not api_base:
                    api_base = channel_conf.get("api_base") or None
                if not api_base:
                    ch_base_env = channel_conf.get("api_base_env", "")
                    if ch_base_env:
                        api_base = os.getenv(ch_base_env)
                # 渠道的 api_key: 直接值 > 环境变量
                if not api_key:
                    api_key = channel_conf.get("api_key") or None
                if not api_key:
                    ch_key_env = channel_conf.get("api_key_env", "")
                    if ch_key_env:
                        api_key = os.getenv(ch_key_env)

        return api_base, api_key

    def _is_custom_channel(self, model_conf: dict) -> bool:
        """判断模型是否使用自定义渠道"""
        provider_name = model_conf.get("provider", "")
        if provider_name:
            channels = self.config.get("channels", {})
            channel_conf = channels.get(provider_name, {})
            return channel_conf.get("type") == "custom" or channel_conf.get("provider") == "custom"
        return False

    def _get_compatibility_prefix(self, model_conf: dict) -> str:
        """获取自定义渠道的兼容类型前缀"""
        provider_name = model_conf.get("provider", "")
        if not provider_name:
            return ""
        
        channels = self.config.get("channels", {})
        channel_conf = channels.get(provider_name, {})
        if not channel_conf or channel_conf.get("type") != "custom":
            return ""
        
        compatibility_type = channel_conf.get("compatibility_type", "openai")
        
        # 兼容类型映射到 LiteLLM 前缀
        compatibility_map = {
            "openai": "openai",
            "anthropic": "anthropic",
            "google": "google",
            "azure": "azure",
            "cohere": "cohere",
            "groq": "groq",
            "mistral": "mistral",
            "together": "together",
            "other": "openai"  # 默认使用 OpenAI 兼容
        }
        
        return compatibility_map.get(compatibility_type, "openai")

    def _should_add_prefix(self, model_conf: dict) -> bool:
        """判断是否应该添加前缀
        
        Args:
            model_conf: 模型配置字典
            
        Returns:
            bool: 是否应该添加前缀
        """
        if not self._is_custom_channel(model_conf):
            return False
        
        model_id = model_conf.get("model", "")
        
        # 检查是否以已知提供商前缀开头
        known_providers = ['openai/', 'anthropic/', 'google/', 'azure/', 'cohere/', 'groq/', 'mistral/', 'together/']
        
        for provider in known_providers:
            if model_id.startswith(provider):
                return False  # 已有标准前缀，不需要添加
        
        return True  # 其他情况都需要添加前缀

    def _create_litellm_instance(self, model_conf: dict, prompt: str = ""):
        """创建 litellm 统一模型实例"""
        from .assistant.openai_model import OpenAIAssistant

        api_base, api_key = self._resolve_credentials(model_conf)

        model_id = model_conf.get("model", "gpt-4o-mini")
        # 自定义渠道需要根据兼容类型添加相应前缀
        if self._should_add_prefix(model_conf):
            prefix = self._get_compatibility_prefix(model_conf)
            model_id = f"{prefix}/{model_id}"
            logger.debug(f"自定义渠道模型自动添加 {prefix}/ 前缀: {model_id}")

        return OpenAIAssistant(
            model=model_id,
            api_base=api_base,
            api_key=api_key,
            stream=model_conf.get("stream", True),
            enable_search=model_conf.get("enable_search", False),
            enable_reasoning=model_conf.get("enable_reasoning", False),
            prompt=prompt,
            extra_params=model_conf.get("extra_params", {}),
        )

    def get_model(self, model_key: str):
        """获取或创建模型实例（带缓存）"""
        if model_key in self._model_instances:
            return self._model_instances[model_key]

        models_conf = self.config.models
        if model_key not in models_conf:
            raise ValueError(f"未定义的模型: {model_key}，可用模型: {list(models_conf.keys())}")

        model_conf = models_conf[model_key]
        # 统一使用 LiteLLM，移除类型检查
        instance = self._create_litellm_instance(model_conf)

        self._model_instances[model_key] = instance
        logger.info(f"已创建模型: {model_key} → {model_conf.get('model', '?')}")
        return instance

    def get_role(self, role_key: str):
        """获取或创建角色实例（模型 + Prompt 组合，带缓存）"""
        if role_key in self._role_instances:
            return self._role_instances[role_key]

        roles_conf = self.config.roles
        if role_key not in roles_conf:
            raise ValueError(f"未定义的角色: {role_key}，可用角色: {list(roles_conf.keys())}")

        role_conf = roles_conf[role_key]
        base_model_key = role_conf.get("base_model", "")
        prompt_key = role_conf.get("prompt_key", "")

        prompt = self.config.prompts.get(prompt_key, "")
        if not prompt:
            logger.warning(f"角色 {role_key} 的 prompt_key '{prompt_key}' 未找到对应 prompt")

        models_conf = self.config.models
        if base_model_key not in models_conf:
            raise ValueError(f"角色 {role_key} 引用的基础模型 '{base_model_key}' 未定义")

        model_conf = models_conf[base_model_key]
        # 统一使用 LiteLLM，移除类型检查
        instance = self._create_litellm_instance(model_conf, prompt=prompt)

        self._role_instances[role_key] = instance
        logger.info(f"已创建角色: {role_key} (base={base_model_key}, prompt_key={prompt_key})")
        return instance

    def clear_cache(self):
        """清空所有缓存的实例（用于热重载）"""
        self._model_instances.clear()
        self._role_instances.clear()
        logger.info("模型缓存已清空")
