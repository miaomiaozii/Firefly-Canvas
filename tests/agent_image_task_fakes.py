"""Offline provider doubles at the project image worker's adapter boundary."""
from types import SimpleNamespace
from unittest.mock import patch

from app.routers import api_project_library


def stub_providers(provider_id='mock', model='mock'):
    return [{'id': provider_id, 'name': provider_id, 'image_models': [model]}]


def route_stub(provider_id='mock', model='mock'):
    return SimpleNamespace(provider_id=provider_id, provider_name=provider_id, model=model,
                           reason='test', to_dict=lambda: {'provider_id': provider_id, 'model': model})


def image_candidate(fake):
    async def run(provider_id, model, prompt, size, quality, references, image_parameters, **kwargs):
        result = await fake(SimpleNamespace(provider_id=provider_id, model=model, prompt=prompt,
                                            size=size, quality=quality, n=1, image_parameters=image_parameters))
        items = [{'type': 'url', 'value': item['url'], 'mime_type': item.get('mime_type', 'image/png')}
                 for item in result.get('image_items', [])]
        if not items:
            raise RuntimeError('Mock provider returned no images')
        return items[0], {}
    return run


def patch_candidate(fake):
    return patch.object(api_project_library, 'run_routed_image_candidate', image_candidate(fake))


def patch_archive(archive):
    return patch.object(api_project_library, 'archive_project_generated_image', side_effect=archive)
