#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
配置管理模块 - 负责加载、合并、保存配置
优先级：CLI参数 > 用户配置文件 > 默认配置
"""

import os
import copy
import logging
from pathlib import Path

import yaml

logger = logging.getLogger("multi_ai_assistant")

# 用户配置目录
# 优先使用原始用户的 HOME 目录，避免 sudo 权限问题
def get_original_home():
    """获取原始用户的 HOME 目录，处理 sudo 情况"""
    sudo_user = os.environ.get('SUDO_USER')
    if sudo_user:
        # 如果是通过 sudo 运行，尝试获取原始用户的 HOME
        try:
            import pwd
            return pwd.getpwnam(sudo_user).pw_dir
        except (ImportError, KeyError):
            # 如果无法获取，使用常见的用户目录
            return f"/home/{sudo_user}"
    
    # 如果不是 sudo，使用当前 HOME
    return os.environ.get('HOME') or str(Path.home())

original_home = get_original_home()
USER_CONFIG_DIR = Path(original_home) / ".config" / "multi_ai_assistant"
USER_CONFIG_FILE = USER_CONFIG_DIR / "config.yaml"

# 默认配置文件（项目内置）
DEFAULT_CONFIG_FILE = Path(__file__).parent / "default_config.yaml"


def deep_merge(base: dict, override: dict) -> dict:
    """深度合并两个字典，override 中的值覆盖 base 中的同名值

    Args:
        base: 基础字典
        override: 覆盖字典

    Returns:
        合并后的新字典
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_yaml(path: Path) -> dict:
    """加载 YAML 文件

    Args:
        path: 文件路径

    Returns:
        解析后的字典，文件不存在或解析失败返回空字典
    """
    if not path.exists():
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"加载配置文件失败 {path}: {e}")
        return {}


def save_yaml(data: dict, path: Path):
    """保存字典为 YAML 文件

    Args:
        data: 要保存的字典
        path: 目标文件路径
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    logger.info(f"配置已保存到 {path}")


class ConfigManager:
    """配置管理器

    负责加载默认配置、用户配置和CLI覆盖，提供统一的配置访问接口。
    支持配置热重载和保存到用户配置文件。
    """

    def __init__(self, user_config_path=None):
        """初始化配置管理器

        Args:
            user_config_path: 用户配置文件路径，默认 ~/.config/multi_ai_assistant/config.yaml
        """
        self.user_config_path = Path(user_config_path) if user_config_path else USER_CONFIG_FILE
        self._default_config_path = DEFAULT_CONFIG_FILE
        self._default_config = {}
        self._user_config = {}
        self._cli_overrides = {}
        self._merged_config = {}
        self.load()

    def load(self):
        """加载并合并所有配置源"""
        self._default_config = load_yaml(DEFAULT_CONFIG_FILE)
        self._user_config = load_yaml(self.user_config_path)
        self._rebuild()

    def _rebuild(self):
        """重建合并后的配置"""
        merged = deep_merge(self._default_config, self._user_config)
        self._merged_config = deep_merge(merged, self._cli_overrides)

    def apply_cli_overrides(self, overrides: dict):
        """应用 CLI 参数覆盖

        Args:
            overrides: CLI 参数字典
        """
        self._cli_overrides = overrides
        self._rebuild()

    def get(self, key_path: str, default=None):
        """通过点分路径获取配置值

        Args:
            key_path: 点分路径，如 "tts.engine" 或 "models.gemini.model_name"
            default: 默认值

        Returns:
            配置值，不存在时返回 default

        Examples:
            >>> config.get("tts.engine")
            'server'
            >>> config.get("models.gemini.model_name")
            'gemini-2.0-flash'
        """
        keys = key_path.split('.')
        value = self._merged_config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    def set(self, key_path: str, value):
        """设置用户配置值（写入用户层，不影响默认层）

        Args:
            key_path: 点分路径
            value: 配置值
        """
        keys = key_path.split('.')
        d = self._user_config
        for k in keys[:-1]:
            if k not in d or not isinstance(d[k], dict):
                d[k] = {}
            d = d[k]
        d[keys[-1]] = value
        self._rebuild()

    def save_user_config(self):
        """保存当前用户配置到文件"""
        save_yaml(self._user_config, self.user_config_path)

    def delete(self, key_path: str):
        """删除用户配置中的指定键
        
        Args:
            key_path: 点分路径，如 "models.gemini"
        """
        keys = key_path.split('.')
        d = self._user_config
        
        # 导航到父级字典
        for k in keys[:-1]:
            if isinstance(d, dict) and k in d:
                d = d[k]
            else:
                return  # 键不存在，直接返回
        
        # 删除最后一个键
        last_key = keys[-1]
        if isinstance(d, dict) and last_key in d:
            del d[last_key]
            self._rebuild()

    def init_user_config(self):
        """初始化用户配置文件（如果不存在则从默认配置复制）"""
        if not self.user_config_path.exists():
            self.user_config_path.parent.mkdir(parents=True, exist_ok=True)
            # 复制默认配置作为用户配置的起点
            save_yaml(self._default_config, self.user_config_path)
            logger.info(f"已创建用户配置文件: {self.user_config_path}")

    @property
    def config(self) -> dict:
        """获取完整的合并后配置字典"""
        return self._merged_config

    @property
    def user_config(self) -> dict:
        """获取用户层配置字典"""
        return self._user_config

    @property
    def default_config(self) -> dict:
        """获取默认配置字典"""
        return self._default_config

    # ===== 便捷属性 =====

    @property
    def models(self) -> dict:
        return self.get("models", {})

    @property
    def roles(self) -> dict:
        return self.get("roles", {})

    @property
    def prompts(self) -> dict:
        return self.get("prompts", {})

    @property
    def hotkeys(self) -> dict:
        return self.get("hotkeys", {})

    @property
    def web_config(self) -> dict:
        return self.get("web", {})

    @property
    def logging_config(self) -> dict:
        return self.get("logging", {})


# 全局单例
_config_instance = None


def get_config(user_config_path=None) -> ConfigManager:
    """获取全局配置管理器单例

    Args:
        user_config_path: 用户配置文件路径（仅首次调用有效）

    Returns:
        ConfigManager 实例
    """
    global _config_instance
    if _config_instance is None:
        _config_instance = ConfigManager(user_config_path)
    return _config_instance


def reload_config():
    """重新加载配置"""
    global _config_instance
    if _config_instance is not None:
        _config_instance.load()
        logger.info("配置已重新加载")
