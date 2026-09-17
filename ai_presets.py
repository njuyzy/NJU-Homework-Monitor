"""Official provider presets, checked against vendor documentation on 2026-09-17."""

PRESETS = {
    'custom': {
        'label': '自定义（兼容接口）', 'base_url': '', 'models': (), 'vision': False,
        'note': '填写服务商提供的 API 地址和模型 ID，也可接入本地模型。',
        'docs': '',
    },
    'deepseek': {
        'label': 'DeepSeek', 'base_url': 'https://api.deepseek.com',
        'aliases': ('https://api.deepseek.com/v1',),
        'models': ('deepseek-flash', 'deepseek-v4-pro', 'deepseek-v4'), 'vision': False,
        'note': '使用 DeepSeek 开放平台 API Key；图片输入请确认所选模型支持后开启。',
        'docs': 'https://api-docs.deepseek.com/',
    },
    'openai': {
        'label': 'GPT（OpenAI）', 'base_url': 'https://api.openai.com/v1',
        'models': ('gpt-5.4', 'gpt-5.4-mini', 'gpt-5.3'), 'vision': True,
        'note': '使用 OpenAI API Key；模型 ID 可直接修改。ChatGPT 订阅不能代替 API Key。',
        'docs': 'https://developers.openai.com/api/docs/models/gpt-5.4',
    },
    'anthropic': {
        'label': 'Claude（Anthropic）', 'base_url': 'https://api.anthropic.com/v1',
        'models': ('claude-sonnet-5', 'claude-opus-5', 'claude-haiku-4-5-20251001'), 'vision': True,
        'note': '使用 Claude API Key，通过官方兼容接口连接；支持文本、图片和工具调用。',
        'docs': 'https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk',
    },
    'kimi': {
        'label': 'Kimi（国内）', 'base_url': 'https://api.moonshot.cn/v1',
        'models': ('kimi-k3', 'kimi-k2.7-code-highspeed', 'kimi-k2.6'), 'vision': True,
        'note': '使用 Kimi 国内开放平台的 API Key；国际账号请选择 Kimi（国际）。',
        'docs': 'https://platform.kimi.com/docs/get-api-key',
    },
    'kimi_global': {
        'label': 'Kimi（国际）', 'base_url': 'https://api.moonshot.ai/v1',
        'models': ('kimi-k3', 'kimi-k2.7-code-highspeed', 'kimi-k2.6'), 'vision': True,
        'note': '使用 Kimi 国际开放平台的 API Key；与国内站分别保存配置。',
        'docs': 'https://platform.kimi.ai/docs/overview',
    },
    'gemini': {
        'label': 'Gemini（Google）', 'base_url': 'https://generativelanguage.googleapis.com/v1beta/openai',
        'models': ('gemini-3.8-flash', 'gemini-3.8-pro', 'gemini-3.5-pro', 'gemini-3.5-flash'), 'vision': True,
        'note': '使用 Google AI Studio 的 Gemini API Key，地址已配置为官方兼容接口。',
        'docs': 'https://ai.google.dev/gemini-api/docs/openai',
    },
    'grok': {
        'label': 'Grok（xAI）', 'base_url': 'https://api.x.ai/v1',
        'models': ('grok-4.6', 'grok-4.6-fast', 'grok-4.5', 'grok-4.5-mini'), 'vision': True,
        'note': '使用 xAI 开放平台 API Key；可手动填写账号有权限使用的其他模型。',
        'docs': 'https://docs.x.ai/developers/grok-4-6',
    },
}


def detect_provider(base_url):
    url = str(base_url).strip().rstrip('/').removesuffix('/chat/completions')
    for name, preset in PRESETS.items():
        if url and url in (preset['base_url'], *preset.get('aliases', ())):
            return name
    return 'custom'


def preset_configuration(provider):
    preset = PRESETS[provider]
    return {'provider': provider, 'base_url': preset['base_url'],
            'model': next(iter(preset['models']), ''), 'api_key': '',
            'stream': True, 'vision': preset['vision'], 'timeout': 180, 'max_rounds': 24}
