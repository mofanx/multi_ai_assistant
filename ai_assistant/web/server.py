#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Web 管理界面后端 - 基于 FastAPI
提供配置管理、状态查询、快捷键管理等 REST API
"""

import os
import threading
import logging
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any

logger = logging.getLogger("multi_ai_assistant")

# 全局引用，由 start_web_server 注入
_config = None
_factory = None
_reload_callback = None

app = FastAPI(title="Multi AI Assistant", version="2.1.0")

# 静态文件
STATIC_DIR = Path(__file__).parent / "static"


# ===== Pydantic 模型 =====

class ConfigUpdateRequest(BaseModel):
    key_path: str
    value: Any


class PromptUpdateRequest(BaseModel):
    key: str
    value: str


class HotkeyUpdateRequest(BaseModel):
    hotkey: str
    action: str
    target: Optional[str] = ""


class ModelUpdateRequest(BaseModel):
    key: str
    config: Dict[str, Any]


class RoleUpdateRequest(BaseModel):
    key: str
    config: Dict[str, Any]


# ===== API 路由 =====

@app.get("/api/status")
def get_status():
    """获取系统运行状态"""
    return {
        "status": "running",
        "version": "2.1.0",
        "config_path": str(_config.user_config_path) if _config else "",
        "model_count": len(_config.models) if _config else 0,
        "hotkey_count": len(_config.hotkeys) if _config else 0,
    }


@app.get("/api/config")
def get_config():
    """获取完整配置（脱敏）"""
    if not _config:
        raise HTTPException(status_code=500, detail="配置未初始化")
    return _config.config


@app.post("/api/config/set")
def set_config(req: ConfigUpdateRequest):
    """设置配置项"""
    if not _config:
        raise HTTPException(status_code=500, detail="配置未初始化")
    _config.set(req.key_path, req.value)
    _config.save_user_config()
    return {"ok": True, "key_path": req.key_path}


# ----- Environment Check -----

@app.get("/api/env-check")
def check_env():
    """检查所有模型的环境变量配置状态"""
    if not _config:
        raise HTTPException(status_code=500, detail="配置未初始化")
    result = {}
    for key, conf in _config.models.items():
        api_key_env = conf.get("api_key_env", "")
        api_base_env = conf.get("api_base_env", "")
        result[key] = {
            "model": conf.get("model", "?"),
            "api_key_ok": bool(os.getenv(api_key_env)) if api_key_env else None,
            "api_base_ok": bool(os.getenv(api_base_env)) if api_base_env else None,
            "auto_resolve": not api_key_env and not api_base_env,
        }
    return result


# ----- Available Models (litellm database) -----

@app.get("/api/models/available")
def list_available_models(q: str = "", provider: str = "", limit: int = 50):
    """搜索 litellm 支持的模型"""
    try:
        import litellm
        db = litellm.model_cost
    except Exception:
        raise HTTPException(status_code=500, detail="无法加载 litellm 模型数据库")

    results = []
    q_lower = q.lower()
    p_lower = provider.lower()

    for model_id, info in db.items():
        if model_id == "sample_spec":
            continue
        m_provider = info.get("litellm_provider", "")
        m_mode = info.get("mode", "")
        if m_mode != "chat":
            continue
        if p_lower and p_lower not in m_provider.lower():
            continue
        if q_lower and q_lower not in model_id.lower():
            continue
        results.append({
            "model": model_id,
            "provider": m_provider,
            "max_input_tokens": info.get("max_input_tokens", 0),
            "max_output_tokens": info.get("max_output_tokens", 0),
            "supports_vision": info.get("supports_vision", False),
            "supports_function_calling": info.get("supports_function_calling", False),
        })
        if len(results) >= limit:
            break
    return {"count": len(results), "models": results}


@app.get("/api/models/providers")
def list_available_providers():
    """列出所有提供商（包括 litellm 标准提供商和用户自定义渠道）"""
    try:
        import litellm
        db = litellm.model_cost
    except Exception:
        raise HTTPException(status_code=500, detail="无法加载 litellm 模型数据库")

    providers = {}
    
    # 1. 加载 litellm 标准提供商
    for model_id, info in db.items():
        if model_id == "sample_spec":
            continue
        p = info.get("litellm_provider", "unknown")
        mode = info.get("mode", "")
        if p not in providers:
            providers[p] = {"total": 0, "chat": 0, "type": "standard"}
        providers[p]["total"] += 1
        if mode == "chat":
            providers[p]["chat"] += 1
    
    # 2. 添加用户配置的自定义渠道
    if not _config:
        raise HTTPException(status_code=500, detail="配置未初始化")
    
    custom_providers = {}
    for key, model_conf in _config.models.items():
        model_id = model_conf.get("model", "")
        api_base_env = model_conf.get("api_base_env")
        api_key_env = model_conf.get("api_key_env")
        
        # 如果有自定义 API Base，则视为自定义渠道
        if api_base_env and model_id.startswith("openai/"):
            provider_name = f"custom_{key}"
            # 从模型标识符中提取渠道名，但保持简单
            channel = key  # 使用模型 key 作为渠道名
            provider_name = f"custom_{channel}"
            
            if provider_name not in providers:
                providers[provider_name] = {"total": 0, "chat": 0, "type": "custom", "model_key": key}
                custom_providers[provider_name] = {
                    "model_key": key,
                    "model_id": model_id,
                    "api_base_env": api_base_env,
                    "api_key_env": api_key_env
                }
            
            providers[provider_name]["total"] += 1
            providers[provider_name]["chat"] += 1
    
    return {
        "providers": providers,
        "custom_providers": custom_providers
    }


# ----- Provider Management -----

@app.get("/api/providers/config")
def get_providers_config():
    """获取用户配置的渠道列表"""
    if not _config:
        raise HTTPException(status_code=500, detail="配置未初始化")
    
    # 从配置的 channels 部分获取渠道信息
    channels = _config.get("channels", {})
    
    providers = {}
    for key, channel_conf in channels.items():
        provider_type = "custom" if channel_conf.get("provider") == "custom" else "standard"
        
        providers[key] = {
            "name": key,
            "type": provider_type,
            "provider": channel_conf.get("provider", ""),
            "api_base": channel_conf.get("api_base"),
            "api_base_env": channel_conf.get("api_base_env"),
            "api_key": channel_conf.get("api_key"),
            "api_key_env": channel_conf.get("api_key_env"),
            "description": channel_conf.get("description", ""),
            "healthy": channel_conf.get("healthy"),
            "last_check": channel_conf.get("last_check")
        }
    
    return providers

@app.post("/api/providers/test")
def test_provider_config(req: dict):
    """测试渠道配置"""
    if not _config:
        raise HTTPException(status_code=500, detail="配置未初始化")
    
    provider_type = req.get("type")
    
    if not provider_type:
        raise HTTPException(status_code=400, detail="请选择渠道类型")
    
    if provider_type == "standard":
        provider_name = req.get("provider")
        api_key = req.get("api_key")
        api_key_env = req.get("api_key_env")
        
        if not provider_name:
            raise HTTPException(status_code=400, detail="请选择标准提供商")
        
        if not api_key and not api_key_env:
            raise HTTPException(status_code=400, detail="请填写 API Key 或 API Key 环境变量名（至少一个）")
        
        # 测试标准提供商连接
        try:
            import os
            import litellm
            import datetime
            
            # 准备测试参数
            test_params = {
                "model": f"{provider_name}/gpt-3.5-turbo",
                "messages": [{"role": "user", "content": "test"}],
                "max_tokens": 1  # 最小 token 数量，只测试连接
            }
            
            if api_key:
                test_params["api_key"] = api_key
            elif api_key_env:
                test_key = os.getenv(api_key_env, "")
                if not test_key:
                    return {
                        "success": False,
                        "error": f"环境变量 {api_key_env} 未设置或为空",
                        "error_type": "env_var_missing"
                    }
                test_params["api_key"] = test_key
            
            # 使用最小 token 测试连接，避免耗费过多资源
            response = litellm.completion(**test_params)
            
            # 更新健康状态
            _config.set(f"channels.{provider_name}.healthy", True)
            _config.set(f"channels.{provider_name}.last_check", datetime.datetime.now().isoformat())
            _config.save_user_config()
            
            return {
                "success": True,
                "message": f"连接 {provider_name} 成功",
                "provider": provider_name,
                "test_result": "connection_ok",
                "healthy": True,
                "note": "连接测试成功，可在模型管理中添加具体模型"
            }
            
        except Exception as e:
            error_msg = str(e)
            if "API key" in error_msg.lower() or "authentication" in error_msg.lower():
                error_type = "auth_failed"
                user_msg = "API Key 认证失败，请检查 API Key 是否正确"
            elif "rate limit" in error_msg.lower():
                error_type = "rate_limit"
                user_msg = "API 调用频率限制，请稍后重试"
            elif "model" in error_msg.lower() and "not found" in error_msg.lower():
                error_type = "model_not_found"
                user_msg = "模型不存在，请检查提供商名称"
            else:
                error_type = "connection_failed"
                user_msg = f"连接失败: {error_msg}"
            
            # 更新健康状态
            _config.set(f"channels.{provider_name}.healthy", False)
            _config.set(f"channels.{provider_name}.last_check", datetime.datetime.now().isoformat())
            _config.save_user_config()
            
            return {
                "success": False,
                "error": user_msg,
                "error_type": error_type,
                "provider": provider_name,
                "healthy": False
            }
    
    elif provider_type == "custom":
        name = req.get("name")
        api_base = req.get("api_base")
        api_base_env = req.get("api_base_env")
        api_key = req.get("api_key")
        api_key_env = req.get("api_key_env")
        
        if not name:
            raise HTTPException(status_code=400, detail="请填写渠道名称")
        
        # API Base 配置（至少一个）
        if not api_base and not api_base_env:
            raise HTTPException(status_code=400, detail="请填写 API Base URL 或 API Base 环境变量名（至少一个）")
        
        # API Key 配置（至少一个）
        if not api_key and not api_key_env:
            raise HTTPException(status_code=400, detail="请填写 API Key 或 API Key 环境变量名（至少一个）")
        
        # 测试自定义渠道连接
        try:
            import os
            import requests
            import datetime
            
            # 获取 API Base
            if not api_base and api_base_env:
                api_base = os.getenv(api_base_env, "")
                if not api_base:
                    return {
                        "success": False,
                        "error": f"环境变量 {api_base_env} 未设置或为空",
                        "error_type": "env_var_missing"
                    }
            
            # 获取 API Key
            if not api_key and api_key_env:
                api_key = os.getenv(api_key_env, "")
                if not api_key:
                    return {
                        "success": False,
                        "error": f"环境变量 {api_key_env} 未设置或为空",
                        "error_type": "env_var_missing"
                    }
            
            # 测试 OpenAI 兼容 API
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            
            # 调用 /models 端点测试连接
            models_url = f"{api_base.rstrip('/')}/models"
            response = requests.get(models_url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                try:
                    models_data = response.json()
                    model_count = len(models_data.get("data", []))
                    
                    # 更新健康状态
                    _config.set(f"channels.{name}.healthy", True)
                    _config.set(f"channels.{name}.last_check", datetime.datetime.now().isoformat())
                    _config.save_user_config()
                    
                    return {
                        "success": True,
                        "message": f"连接自定义渠道 {name} 成功",
                        "channel_name": name,
                        "api_base": api_base,
                        "available_models": model_count,
                        "test_result": "connection_ok",
                        "healthy": True
                    }
                except Exception as json_error:
                    # 更新健康状态
                    _config.set(f"channels.{name}.healthy", False)
                    _config.set(f"channels.{name}.last_check", datetime.datetime.now().isoformat())
                    _config.save_user_config()
                    
                    return {
                        "success": False,
                        "error": f"API 响应格式错误: 无法解析 JSON",
                        "error_type": "json_parse_error",
                        "status_code": response.status_code,
                        "response_text": response.text[:200],
                        "healthy": False
                    }
            else:
                # 更新健康状态
                _config.set(f"channels.{name}.healthy", False)
                _config.set(f"channels.{name}.last_check", datetime.datetime.now().isoformat())
                _config.save_user_config()
                
                return {
                    "success": False,
                    "error": f"API 调用失败: HTTP {response.status_code}",
                    "error_type": "api_error",
                    "status_code": response.status_code,
                    "response_text": response.text[:200],
                    "healthy": False
                }
                
        except requests.exceptions.Timeout:
            # 更新健康状态
            _config.set(f"channels.{name}.healthy", False)
            _config.set(f"channels.{name}.last_check", datetime.datetime.now().isoformat())
            _config.save_user_config()
            
            return {
                "success": False,
                "error": "连接超时，请检查网络和 API Base 地址",
                "error_type": "timeout",
                "healthy": False
            }
        except requests.exceptions.ConnectionError:
            # 更新健康状态
            _config.set(f"channels.{name}.healthy", False)
            _config.set(f"channels.{name}.last_check", datetime.datetime.now().isoformat())
            _config.save_user_config()
            
            return {
                "success": False,
                "error": "连接失败，请检查 API Base 地址",
                "error_type": "connection_error",
                "healthy": False
            }
        except Exception as e:
            # 更新健康状态
            _config.set(f"channels.{name}.healthy", False)
            _config.set(f"channels.{name}.last_check", datetime.datetime.now().isoformat())
            _config.save_user_config()
            
            return {
                "success": False,
                "error": f"测试失败: {str(e)}",
                "error_type": "unknown_error",
                "healthy": False
            }
    
    else:
        raise HTTPException(status_code=400, detail="无效的渠道类型")

@app.post("/api/providers/config")
def save_provider_config(req: dict):
    """保存渠道配置"""
    if not _config:
        raise HTTPException(status_code=500, detail="配置未初始化")
    
    provider_type = req.get("type")
    
    if not provider_type:
        raise HTTPException(status_code=400, detail="请选择渠道类型")
    
    if provider_type == "standard":
        provider_name = req.get("provider")
        api_key = req.get("api_key")
        api_key_env = req.get("api_key_env")
        
        if not provider_name:
            raise HTTPException(status_code=400, detail="请选择标准提供商")
        
        if not api_key and not api_key_env:
            raise HTTPException(status_code=400, detail="请填写 API Key 或 API Key 环境变量名（至少一个）")
        
        # 创建渠道配置（不是模型配置）
        channel_config = {
            "type": "standard",
            "provider": provider_name,
            "description": f"标准渠道: {provider_name}",
            "healthy": None,  # 初始状态为未检查
            "last_check": None
        }
        
        # API Key 配置
        if api_key:
            channel_config["api_key"] = api_key
        if api_key_env:
            channel_config["api_key_env"] = api_key_env
        
        # 保存到配置文件的 channels 部分
        _config.set(f"channels.{provider_name}", channel_config)
        _config.save_user_config()
        
        return {"message": f"标准渠道 '{provider_name}' 已保存，请在模型管理中添加具体模型"}
    
    elif provider_type == "custom":
        name = req.get("name")
        api_base = req.get("api_base")
        api_base_env = req.get("api_base_env")
        api_key = req.get("api_key")
        api_key_env = req.get("api_key_env")
        description = req.get("description", "")
        
        if not name:
            raise HTTPException(status_code=400, detail="请填写渠道名称")
        
        # API Base 配置（至少一个）
        if not api_base and not api_base_env:
            raise HTTPException(status_code=400, detail="请填写 API Base URL 或 API Base 环境变量名（至少一个）")
        
        # API Key 配置（至少一个）
        if not api_key and not api_key_env:
            raise HTTPException(status_code=400, detail="请填写 API Key 或 API Key 环境变量名（至少一个）")
        
        # 创建渠道配置（不是模型配置）
        channel_config = {
            "type": "custom",
            "provider": "custom",
            "description": description or f"自定义渠道: {name}",
            "healthy": None,  # 初始状态为未检查
            "last_check": None
        }
        
        # API Base 配置
        if api_base:
            channel_config["api_base"] = api_base
        if api_base_env:
            channel_config["api_base_env"] = api_base_env
        
        # API Key 配置
        if api_key:
            channel_config["api_key"] = api_key
        if api_key_env:
            channel_config["api_key_env"] = api_key_env
        
        # 保存到配置文件的 channels 部分
        _config.set(f"channels.{name}", channel_config)
        _config.save_user_config()
        
        return {"message": f"自定义渠道 '{name}' 已保存，请在模型管理中添加具体模型"}
    
    else:
        raise HTTPException(status_code=400, detail="无效的渠道类型")

@app.delete("/api/providers/config/{name}")
def delete_provider_config(name: str):
    """删除渠道配置"""
    if not _config:
        raise HTTPException(status_code=500, detail="配置未初始化")
    
    channels = _config.get("channels", {})
    if name in channels:
        _config.delete(f"channels.{name}")
        _config.save_user_config()
        return {"message": f"渠道 '{name}' 已删除"}
    else:
        raise HTTPException(status_code=404, detail=f"未找到渠道: {name}")

@app.get("/api/providers/{name}/models")
def get_provider_models(name: str):
    """获取指定渠道的可用模型列表"""
    if not _config:
        raise HTTPException(status_code=500, detail="配置未初始化")
    
    # 检查渠道是否存在
    channels = _config.get("channels", {})
    if name in channels:
        channel_conf = channels[name]
        api_base_env = channel_conf.get("api_base_env")
        api_key_env = channel_conf.get("api_key_env")
        api_base = channel_conf.get("api_base")
        api_key = channel_conf.get("api_key")
        provider = channel_conf.get("provider", "")
        
        # 对于自定义渠道，尝试获取模型列表
        if provider == "custom" and (api_base or api_base_env):
            try:
                import os
                import requests
                
                # 优先使用直接配置的值
                if not api_base and api_base_env:
                    api_base = os.getenv(api_base_env, "")
                if not api_key and api_key_env:
                    api_key = os.getenv(api_key_env, "")
                
                if not api_base or not api_key:
                    return {
                        "error": "环境变量未设置",
                        "api_base_env": api_base_env,
                        "api_key_env": api_key_env,
                        "models": []
                    }
                
                # 调用 OpenAI 兼容 API 获取模型列表
                headers = {"Authorization": f"Bearer {api_key}"}
                response = requests.get(f"{api_base.rstrip('/')}/models", headers=headers, timeout=10)
                response.raise_for_status()
                
                models_data = response.json()
                models = [{"id": m.get("id"), "object": m.get("object")} for m in models_data.get("data", [])]
                
                return {"provider": name, "models": models, "total": len(models)}
                
            except Exception as e:
                return {
                    "error": str(e),
                    "api_base_env": api_base_env,
                    "api_key_env": api_key_env,
                    "models": []
                }
    
    # 如果不是自定义渠道，返回标准提供商的模型
    try:
        import litellm
        db = litellm.model_cost
        
        models = []
        for model_id, info in db.items():
            if model_id == "sample_spec":
                continue
            provider = info.get("litellm_provider", "")
            mode = info.get("mode", "")
            if provider == name and mode == "chat":
                models.append({
                    "id": model_id,
                    "max_input_tokens": info.get("max_input_tokens", 0),
                    "supports_vision": info.get("supports_vision", False),
                    "supports_function_calling": info.get("supports_function_calling", False)
                })
        
        return {"provider": name, "models": models, "total": len(models)}
        
    except Exception as e:
        return {"error": str(e), "models": []}

# ----- Configured Models -----

@app.get("/api/models/custom/{provider_name}")
def list_custom_provider_models(provider_name: str):
    """获取自定义渠道的可用模型列表"""
    if not _config:
        raise HTTPException(status_code=500, detail="配置未初始化")
    
    # 查找对应的自定义渠道模型配置
    custom_model = None
    for key, model_conf in _config.models.items():
        model_id = model_conf.get("model", "")
        api_base_env = model_conf.get("api_base_env")
        
        if api_base_env and model_id.startswith("openai/"):
            if f"custom_{key}" == provider_name:
                custom_model = model_conf
                break
    
    if not custom_model:
        raise HTTPException(status_code=404, detail=f"未找到自定义渠道: {provider_name}")
    
    # 尝试获取该渠道的模型列表
    try:
        import os
        import requests
        
        api_base = os.getenv(custom_model.get("api_base_env", ""))
        api_key = os.getenv(custom_model.get("api_key_env", ""))
        
        if not api_base or not api_key:
            return {
                "error": "环境变量未设置",
                "api_base_env": custom_model.get("api_base_env"),
                "api_key_env": custom_model.get("api_key_env"),
                "models": []
            }
        
        # 调用 OpenAI 兼容 API 获取模型列表
        headers = {"Authorization": f"Bearer {api_key}"}
        response = requests.get(f"{api_base.rstrip('/')}/models", headers=headers, timeout=10)
        response.raise_for_status()
        
        models_data = response.json()
        models = []
        
        for model in models_data.get("data", []):
            models.append({
                "id": model.get("id"),
                "object": model.get("object"),
                "created": model.get("created"),
                "owned_by": model.get("owned_by")
            })
        
        return {
            "provider": provider_name,
            "api_base": api_base,
            "models": models,
            "total": len(models)
        }
        
    except Exception as e:
        return {
            "error": str(e),
            "provider": provider_name,
            "api_base_env": custom_model.get("api_base_env"),
            "api_key_env": custom_model.get("api_key_env"),
            "models": []
        }


@app.get("/api/models")
def list_models():
    """列出所有已配置的模型"""
    if not _config:
        raise HTTPException(status_code=500, detail="配置未初始化")
    return _config.models


@app.get("/api/models/type-info")
def get_model_type_info(model_name: str):
    """获取模型类型信息"""
    if not _factory:
        raise HTTPException(status_code=500, detail="模型工厂未初始化")
    
    try:
        type_info = _factory.get_model_type_info(model_name)
        return type_info
    except Exception as e:
        logger.error(f"获取模型类型信息失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取模型类型信息失败: {str(e)}")


@app.post("/api/models")
def update_model(req: ModelUpdateRequest):
    """添加或更新模型配置"""
    if not _config:
        raise HTTPException(status_code=500, detail="配置未初始化")
    _config.set(f"models.{req.key}", req.config)
    _config.save_user_config()
    return {"ok": True, "key": req.key}


@app.delete("/api/models/{key:path}")
def delete_model(key: str):
    """删除模型配置"""
    if not _config:
        raise HTTPException(status_code=500, detail="配置未初始化")
    
    models = _config.get("models", {})
    if key not in models:
        raise HTTPException(status_code=404, detail=f"未找到模型: {key}")
    
    # 检查是否有快捷键引用此模型
    hotkeys = _config.get("hotkeys", {})
    dependent_hotkeys = []
    
    for hk_key, hk_config in hotkeys.items():
        action = hk_config.get("action")
        target = hk_config.get("target")
        
        if action == "chat" and target == key:
            dependent_hotkeys.append(hk_key)
        elif action == "role":
            # 检查角色是否使用此模型
            roles = _config.get("roles", {})
            role_config = roles.get(target)
            if role_config and role_config.get("base_model") == key:
                dependent_hotkeys.append(f"{hk_key} (通过角色: {target})")
    
    if dependent_hotkeys:
        hotkey_list = ", ".join(dependent_hotkeys)
        raise HTTPException(
            status_code=409, 
            detail=f"无法删除模型 '{key}'，以下快捷键正在使用它: {hotkey_list}。请先删除或修改这些快捷键。"
        )
    
    # 执行删除
    del models[key]
    _config.set("models", models)
    _config.save_user_config()
    return {"ok": True, "key": key}


# ----- Roles -----

@app.get("/api/roles")
def list_roles():
    """列出所有角色配置"""
    if not _config:
        raise HTTPException(status_code=500, detail="配置未初始化")
    return _config.roles


@app.post("/api/roles")
def update_role(req: RoleUpdateRequest):
    """添加或更新角色配置"""
    if not _config:
        raise HTTPException(status_code=500, detail="配置未初始化")
    _config.set(f"roles.{req.key}", req.config)
    _config.save_user_config()
    return {"ok": True, "key": req.key}


@app.delete("/api/roles/{key}")
def delete_role(key: str):
    """删除角色配置"""
    if not _config:
        raise HTTPException(status_code=500, detail="配置未初始化")
    roles = _config.get("roles", {})
    if key in roles:
        del roles[key]
        _config.set("roles", roles)
        _config.save_user_config()
    return {"ok": True, "key": key}


# ----- Prompts -----

@app.get("/api/prompts")
def list_prompts():
    """列出所有 Prompt 模板"""
    if not _config:
        raise HTTPException(status_code=500, detail="配置未初始化")
    return _config.prompts


@app.post("/api/prompts")
def update_prompt(req: PromptUpdateRequest):
    """添加或更新 Prompt"""
    if not _config:
        raise HTTPException(status_code=500, detail="配置未初始化")
    _config.set(f"prompts.{req.key}", req.value)
    _config.save_user_config()
    return {"ok": True, "key": req.key}


@app.delete("/api/prompts/{key}")
def delete_prompt(key: str):
    """删除 Prompt"""
    if not _config:
        raise HTTPException(status_code=500, detail="配置未初始化")
    prompts = _config.get("prompts", {})
    if key in prompts:
        del prompts[key]
        _config.set("prompts", prompts)
        _config.save_user_config()
    return {"ok": True, "key": key}


# ----- Hotkeys -----

@app.get("/api/hotkeys")
def list_hotkeys():
    """列出所有快捷键配置"""
    if not _config:
        raise HTTPException(status_code=500, detail="配置未初始化")
    return _config.hotkeys


@app.post("/api/hotkeys")
def update_hotkey(req: HotkeyUpdateRequest):
    """添加或更新快捷键"""
    if not _config:
        raise HTTPException(status_code=500, detail="配置未初始化")
    binding = {"action": req.action}
    if req.target:
        binding["target"] = req.target
    _config.set(f"hotkeys.{req.hotkey}", binding)
    _config.save_user_config()
    return {"ok": True, "hotkey": req.hotkey}


@app.delete("/api/hotkeys/{hotkey}")
def delete_hotkey(hotkey: str):
    """删除快捷键"""
    if not _config:
        raise HTTPException(status_code=500, detail="配置未初始化")
    hotkeys = _config.get("hotkeys", {})
    if hotkey in hotkeys:
        del hotkeys[hotkey]
        _config.set("hotkeys", hotkeys)
        _config.save_user_config()
    return {"ok": True, "hotkey": hotkey}


# ----- 配置重载 -----

@app.post("/api/reload")
def reload_config_api():
    """重新加载配置文件（触发热重载）"""
    if _reload_callback:
        _reload_callback()
        return {"ok": True, "message": "配置已热重载"}
    elif _config:
        _config.load()
        return {"ok": True, "message": "配置已重新加载（无热重载回调）"}
    raise HTTPException(status_code=500, detail="配置未初始化")


# ----- 前端页面 -----

@app.get("/")
def serve_index():
    """返回前端页面"""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"message": "Web 管理界面, 请访问 /api/status 查看状态"}


# 挂载静态文件（CSS/JS）
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def start_web_server(config, factory, host="127.0.0.1", port=8199, reload_callback=None):
    """在后台线程启动 Web 服务器

    Args:
        config: ConfigManager 实例
        factory: ModelFactory 实例
        host: 监听地址
        port: 监听端口
        reload_callback: 热重载回调函数
    """
    global _config, _factory, _reload_callback
    _config = config
    _factory = factory
    _reload_callback = reload_callback

    def run():
        uvicorn.run(app, host=host, port=port, log_level="warning")

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    logger.info(f"Web 管理界面后台线程已启动: http://{host}:{port}")
