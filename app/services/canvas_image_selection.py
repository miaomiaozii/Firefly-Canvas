"""#465 admission for canvas image generation (project, Agent and legacy canvas)."""
from firefly_generation import ModelRoutingError, checked_image_model, checked_image_models


def require_canvas_image_provider(provider_id):
    target = str(provider_id or '').strip().lower()
    if not target:
        raise ModelRoutingError('请先选择服务商', code='image_provider_required')
    return target


def select_canvas_image_model(providers, provider_id, model):
    target = require_canvas_image_provider(provider_id)
    provider = next((item for item in providers if item.get('id') == target), None)
    if provider is None or not provider.get('enabled', True):
        label = (provider or {}).get('name') or target
        raise ModelRoutingError(f'服务商 {label} 不存在或未启用，请在全局设置中启用服务商并勾选生图模型。',
                                code='image_provider_unavailable')

    checked = checked_image_models(provider)
    name = provider.get('name') or target
    no_models = f'服务商 {name} 未勾选可用的生图模型，请在全局设置中勾选。'
    model = str(model or '').strip()
    canonical = checked_image_model(provider, model) if model else ''
    if model and not canonical:
        names = list(dict.fromkeys(item.get('name') or item['id'] for item in providers
                                   if item.get('enabled', True) and checked_image_model(item, model)))
        hint = (f'请切换服务商到 {"、".join(names)}，或重新选择模型。' if names
                else '该模型未在任何服务商中启用，请在全局设置里勾选，或换一个模型。')
        raise ModelRoutingError(f'当前模型 {model} 不在服务商 {name} 中。{hint}' + (no_models if not checked and names else ''),
                                code='image_model_not_enabled')
    if not checked:
        raise ModelRoutingError(no_models, code='no_enabled_image_model')
    return provider, canonical


def route_canvas_image(request, payload, providers, route_image_generation):
    provider, model = select_canvas_image_model(providers, payload.provider_id, payload.model)
    # 通用路由允许从发现列表查找手动/变体模型；画布的自动和手动请求都只传本服务商已勾选目录。
    checked_provider = {key: value for key, value in provider.items() if key != 'available_models'}
    manual = payload.routing_mode == 'manual'
    try:
        return route_image_generation(request, [checked_provider],
                                      provider_id=provider['id'] if manual else '', model=model if manual else '')
    except ModelRoutingError as exc:
        if exc.code != 'no_compatible_image_model':
            raise
        name = provider.get('name') or provider['id']
        raise ModelRoutingError(
            f'{exc.message}。服务商 {name}：请在全局设置中勾选兼容的生图模型，或调整参数。',
            code=exc.code, incompatible_parameters=exc.incompatible_parameters,
        ) from exc
