from wh_local.modules.pod_customization.export import analyze_dianxiaomi_export
from wh_local.modules.pod_customization.prompts import LISTING_IMAGE_ROLES


def _batch():
    return {
        'mode': 'fission', 'status': 'generating_pattern', 'requested_count': 2,
        'listing_fields': {'category_name': 'bag'},
        'items': [
            {'style_index': 1, 'role': role, 'status': 'completed',
             'public_url': f'https://cdn.example.com/{role}.jpg'}
            for role in LISTING_IMAGE_ROLES
        ],
        'style_titles': [{'style_index': 1, 'status': 'completed', 'listing_ready': True}],
    }


def test_active_fission_exports_only_ready_styles():
    result = analyze_dianxiaomi_export(_batch(), {
        1: {'title': '包', 'english_title': 'Bag', 'description': 'A bag'}
    })
    assert result.ready
    assert set(result.exportable_styles) == {1}
    assert result.skipped_style_count == 1


def test_fission_pending_title_cannot_export():
    batch = _batch()
    batch['style_titles'][0]['status'] = 'generating'
    result = analyze_dianxiaomi_export(batch, {
        1: {'title': '包', 'english_title': 'Bag', 'description': 'A bag'}
    })
    assert not result.ready


def test_other_modes_still_require_settled_batch():
    batch = _batch()
    batch['mode'] = 'full'
    assert analyze_dianxiaomi_export(batch, {}).block_reason == 'active_batch'
